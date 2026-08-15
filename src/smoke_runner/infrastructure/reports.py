# ruff: noqa: RUF001
"""Report generation and durable SQLite delivery state."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

from sqlalchemy import case, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from smoke_runner.application.ports import ReportCommentator
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import (
    daily_report_schedule,
    first_weekly_delivery_date,
    local_date_of,
    local_day_period,
    resolve_local_datetime,
    weekly_report_schedule,
)
from smoke_runner.domain.metrics import build_daily_metrics, build_weekly_metrics
from smoke_runner.domain.report_models import (
    DailyReportSnapshot,
    ReportType,
    WeeklyReportSnapshot,
    build_daily_report_snapshot,
    build_weekly_report_snapshot,
    commentary_input_from_snapshot,
)
from smoke_runner.domain.timeline import build_timeline
from smoke_runner.infrastructure.db.engine import SessionFactory
from smoke_runner.infrastructure.db.gateway import DashboardFacts, DatabaseGateway
from smoke_runner.infrastructure.db.models import (
    ReportDeliveryPartRow,
    ReportDeliveryRow,
    UserRow,
)

LOGGER = logging.getLogger(__name__)
REPORT_HOUR = 9
MAX_PART_ATTEMPTS = 2


class ReportUnavailableError(RuntimeError):
    """Raised when the requested completed period does not exist for a user."""


@dataclass(frozen=True, slots=True)
class GeneratedReport:
    snapshot: DailyReportSnapshot | WeeklyReportSnapshot
    commentary: str | None = None


@dataclass(frozen=True, slots=True)
class ReportDeliveryWork:
    id: int
    user_id: int
    telegram_chat_id: int
    report_type: ReportType
    period_start: UtcInstant
    period_end: UtcInstant
    snapshot_json: str | None


@dataclass(frozen=True, slots=True)
class ReportPartWork:
    id: int
    delivery_id: int
    ordinal: int
    part_type: Literal["text", "current_week_chart", "history_chart"]
    attempt_count: int


class ReportBuilder:
    """Build immutable report facts from one user's event history."""

    def __init__(
        self,
        gateway: DatabaseGateway,
        commentator: ReportCommentator,
        *,
        commentary_timeout: float = 2.0,
    ) -> None:
        self._gateway = gateway
        self._commentator = commentator
        self._commentary_timeout = commentary_timeout

    async def build_delivery(
        self,
        work: ReportDeliveryWork,
        *,
        generated_at: UtcInstant,
    ) -> GeneratedReport:
        facts = await self._gateway.dashboard_facts(work.user_id)
        if facts is None:
            raise ReportUnavailableError("Active user not found")
        timezone = ZoneInfo(facts.user.timezone_name)
        snapshot: DailyReportSnapshot | WeeklyReportSnapshot
        if work.report_type is ReportType.DAILY:
            reported_date = local_date_of(work.period_end - timedelta(seconds=1), timezone)
            snapshot = self._build_daily(facts, reported_date, generated_at)
        else:
            delivery_sunday = local_date_of(work.period_end, timezone)
            snapshot = self._build_weekly(facts, delivery_sunday, generated_at)
        if (
            snapshot.period.start_at_utc != work.period_start.to_unix_seconds()
            or snapshot.period.end_at_utc != work.period_end.to_unix_seconds()
        ):
            raise ReportUnavailableError("Delivery period no longer matches generated report")
        return GeneratedReport(
            snapshot=snapshot,
            commentary=await self._optional_commentary(facts, snapshot),
        )

    async def build_latest_daily(
        self,
        user_id: int,
        *,
        generated_at: UtcInstant,
    ) -> GeneratedReport:
        facts = await self._required_facts(user_id)
        timezone = ZoneInfo(facts.user.timezone_name)
        reported_date = local_date_of(generated_at, timezone) - timedelta(days=1)
        snapshot = self._build_daily(facts, reported_date, generated_at)
        return GeneratedReport(
            snapshot=snapshot,
            commentary=await self._optional_commentary(facts, snapshot),
        )

    async def build_latest_weekly(
        self,
        user_id: int,
        *,
        generated_at: UtcInstant,
    ) -> GeneratedReport:
        facts = await self._required_facts(user_id)
        timezone = ZoneInfo(facts.user.timezone_name)
        local_today = local_date_of(generated_at, timezone)
        delivery_sunday = local_today - timedelta(days=(local_today.weekday() + 1) % 7)
        first_delivery = first_weekly_delivery_date(facts.user.activated_at, timezone)
        if delivery_sunday < first_delivery:
            raise ReportUnavailableError(
                f"Первый недельный отчёт будет доступен {first_delivery:%d.%m.%Y}."
            )
        snapshot = self._build_weekly(facts, delivery_sunday, generated_at)
        return GeneratedReport(
            snapshot=snapshot,
            commentary=await self._optional_commentary(facts, snapshot),
        )

    async def _required_facts(self, user_id: int) -> DashboardFacts:
        facts = await self._gateway.dashboard_facts(user_id)
        if facts is None:
            raise ReportUnavailableError("Пользователь не найден или доступ отозван.")
        return facts

    def _build_daily(
        self,
        facts: DashboardFacts,
        reported_date: date,
        generated_at: UtcInstant,
    ) -> DailyReportSnapshot:
        timezone = ZoneInfo(facts.user.timezone_name)
        current_period = local_day_period(reported_date, timezone)
        if facts.user.activated_at >= current_period.end:
            raise ReportUnavailableError("За этот день ещё нет отслеживаемого периода.")
        timeline = build_timeline(list(facts.sessions), list(facts.intervals))
        current = build_daily_metrics(
            timeline,
            facts.wakes,
            local_date=reported_date,
            activated_at=facts.user.activated_at,
            timezone=timezone,
        )
        previous_date = reported_date - timedelta(days=1)
        previous_period = local_day_period(previous_date, timezone)
        previous = None
        if facts.user.activated_at < previous_period.end:
            previous = build_daily_metrics(
                timeline,
                facts.wakes,
                local_date=previous_date,
                activated_at=facts.user.activated_at,
                timezone=timezone,
            )
        return build_daily_report_snapshot(
            current,
            previous=previous,
            generated_at=generated_at,
            timezone_name=facts.user.timezone_name,
        )

    def _build_weekly(
        self,
        facts: DashboardFacts,
        delivery_sunday: date,
        generated_at: UtcInstant,
    ) -> WeeklyReportSnapshot:
        timezone = ZoneInfo(facts.user.timezone_name)
        first_delivery = first_weekly_delivery_date(facts.user.activated_at, timezone)
        if delivery_sunday < first_delivery:
            raise ReportUnavailableError("Завершённого недельного периода пока нет.")
        timeline = build_timeline(list(facts.sessions), list(facts.intervals))
        current = build_weekly_metrics(
            timeline,
            facts.wakes,
            schedule=weekly_report_schedule(
                delivery_sunday,
                timezone,
                activated_at=facts.user.activated_at,
            ),
            activated_at=facts.user.activated_at,
            timezone=timezone,
        )
        previous = None
        previous_sunday = delivery_sunday - timedelta(days=7)
        if previous_sunday >= first_delivery:
            previous = build_weekly_metrics(
                timeline,
                facts.wakes,
                schedule=weekly_report_schedule(
                    previous_sunday,
                    timezone,
                    activated_at=facts.user.activated_at,
                ),
                activated_at=facts.user.activated_at,
                timezone=timezone,
            )
        history = []
        history_sunday = first_delivery
        while history_sunday <= delivery_sunday:
            history.append(
                build_weekly_metrics(
                    timeline,
                    facts.wakes,
                    schedule=weekly_report_schedule(
                        history_sunday,
                        timezone,
                        activated_at=facts.user.activated_at,
                    ),
                    activated_at=facts.user.activated_at,
                    timezone=timezone,
                )
            )
            history_sunday += timedelta(days=7)
        return build_weekly_report_snapshot(
            current,
            previous=previous,
            history=history,
            generated_at=generated_at,
            timezone_name=facts.user.timezone_name,
        )

    async def _optional_commentary(
        self,
        facts: DashboardFacts,
        snapshot: DailyReportSnapshot | WeeklyReportSnapshot,
    ) -> str | None:
        if not facts.ai_commentary_enabled:
            return None
        try:
            return await asyncio.wait_for(
                self._commentator.comment(commentary_input_from_snapshot(snapshot)),
                timeout=self._commentary_timeout,
            )
        except Exception:
            LOGGER.warning("Optional report commentary failed", exc_info=True)
            return None


class ReportDeliveryStore:
    """Create, claim and recover report deliveries and their ordered parts."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def recover(self, now: UtcInstant) -> None:
        now_seconds = now.to_unix_seconds()
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(ReportDeliveryPartRow)
                .where(ReportDeliveryPartRow.status == "claimed")
                .values(status="failed_unknown", error_code="restart_after_claim")
            )
            await session.execute(
                update(ReportDeliveryRow)
                .where(ReportDeliveryRow.status == "claimed")
                .values(
                    status="pending",
                    error_code="restart_after_claim",
                    updated_at_utc=now_seconds,
                )
            )

    async def ensure_due_deliveries(self, now: UtcInstant) -> None:
        async with self._session_factory() as session, session.begin():
            users = (await session.scalars(select(UserRow).where(UserRow.status == "active"))).all()
            for user in users:
                timezone = ZoneInfo(user.timezone_name)
                activated_at = UtcInstant.from_unix_seconds(user.activated_at_utc)
                local_now = now.value.astimezone(timezone)
                delivery_date = local_now.date()
                if local_now.time() < time(hour=REPORT_HOUR):
                    delivery_date -= timedelta(days=1)
                daily = daily_report_schedule(delivery_date, timezone)
                if daily.delivery_at <= now and activated_at < daily.period.end:
                    await self._insert_delivery(
                        session,
                        user_id=user.id,
                        report_type=ReportType.DAILY,
                        period_start=max(daily.period.start, activated_at),
                        period_end=daily.period.end,
                        is_partial=activated_at > daily.period.start,
                        now=now,
                    )

                delivery_sunday = delivery_date - timedelta(days=(delivery_date.weekday() + 1) % 7)
                first_delivery = first_weekly_delivery_date(activated_at, timezone)
                if delivery_sunday >= first_delivery:
                    weekly = weekly_report_schedule(
                        delivery_sunday,
                        timezone,
                        activated_at=activated_at,
                    )
                    if weekly.delivery_at <= now:
                        await self._insert_delivery(
                            session,
                            user_id=user.id,
                            report_type=ReportType.WEEKLY,
                            period_start=weekly.period.start,
                            period_end=weekly.period.end,
                            is_partial=weekly.is_partial,
                            now=now,
                        )

    async def next_boundary(self, now: UtcInstant) -> UtcInstant | None:
        async with self._session_factory() as session:
            users = (await session.scalars(select(UserRow).where(UserRow.status == "active"))).all()
        boundaries: list[UtcInstant] = []
        for user in users:
            timezone = ZoneInfo(user.timezone_name)
            local_now = now.value.astimezone(timezone)
            boundary_date = local_now.date()
            boundary = resolve_local_datetime(
                datetime.combine(boundary_date, time(hour=REPORT_HOUR)), timezone
            )
            if boundary <= now:
                boundary = resolve_local_datetime(
                    datetime.combine(boundary_date + timedelta(days=1), time(hour=REPORT_HOUR)),
                    timezone,
                )
            boundaries.append(boundary)
        return min(boundaries, default=None)

    async def claim_due(self, now: UtcInstant) -> ReportDeliveryWork | None:
        now_seconds = now.to_unix_seconds()
        async with self._session_factory() as session, session.begin():
            candidate = (
                await session.execute(
                    select(ReportDeliveryRow, UserRow.telegram_private_chat_id)
                    .join(UserRow, UserRow.id == ReportDeliveryRow.user_id)
                    .where(
                        ReportDeliveryRow.status == "pending",
                        ReportDeliveryRow.period_end_utc <= now_seconds,
                        UserRow.status == "active",
                    )
                    .order_by(
                        ReportDeliveryRow.period_end_utc,
                        case((ReportDeliveryRow.report_type == ReportType.DAILY.value, 0), else_=1),
                        ReportDeliveryRow.id,
                    )
                    .limit(1)
                )
            ).first()
            if candidate is None:
                return None
            row, chat_id = candidate
            claimed = await session.scalar(
                update(ReportDeliveryRow)
                .where(
                    ReportDeliveryRow.id == row.id,
                    ReportDeliveryRow.status == "pending",
                )
                .values(
                    status="claimed",
                    attempt_count=ReportDeliveryRow.attempt_count + 1,
                    claimed_at_utc=now_seconds,
                    updated_at_utc=now_seconds,
                )
                .returning(ReportDeliveryRow.id)
            )
            if claimed is None:
                return None
            return ReportDeliveryWork(
                id=row.id,
                user_id=row.user_id,
                telegram_chat_id=chat_id,
                report_type=ReportType(row.report_type),
                period_start=UtcInstant.from_unix_seconds(row.period_start_utc),
                period_end=UtcInstant.from_unix_seconds(row.period_end_utc),
                snapshot_json=row.snapshot_json,
            )

    async def save_generated(
        self,
        work: ReportDeliveryWork,
        report: GeneratedReport,
        *,
        now: UtcInstant,
    ) -> str:
        payload = asdict(report.snapshot)
        payload["commentary"] = report.commentary
        snapshot_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        part_types = ["text"]
        if work.report_type is ReportType.WEEKLY:
            part_types.extend(("current_week_chart", "history_chart"))
        async with self._session_factory() as session, session.begin():
            row = await session.get(ReportDeliveryRow, work.id)
            if row is None or row.status != "claimed":
                raise RuntimeError("Report delivery claim was lost")
            if row.snapshot_json is None:
                row.snapshot_json = snapshot_json
                row.generated_at_utc = now.to_unix_seconds()
                row.updated_at_utc = now.to_unix_seconds()
                for ordinal, part_type in enumerate(part_types):
                    await session.execute(
                        sqlite_insert(ReportDeliveryPartRow)
                        .values(
                            report_delivery_id=work.id,
                            ordinal=ordinal,
                            part_type=part_type,
                            status="pending",
                            attempt_count=0,
                        )
                        .on_conflict_do_nothing()
                    )
            else:
                snapshot_json = row.snapshot_json
        return snapshot_json

    async def claim_next_part(
        self,
        delivery_id: int,
        *,
        now: UtcInstant,
    ) -> ReportPartWork | None:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(ReportDeliveryPartRow)
                .where(
                    ReportDeliveryPartRow.report_delivery_id == delivery_id,
                    ReportDeliveryPartRow.status != "sent",
                )
                .order_by(ReportDeliveryPartRow.ordinal)
                .limit(1)
            )
            if row is None:
                await session.execute(
                    update(ReportDeliveryRow)
                    .where(
                        ReportDeliveryRow.id == delivery_id,
                        ReportDeliveryRow.status == "claimed",
                    )
                    .values(
                        status="sent",
                        sent_at_utc=now.to_unix_seconds(),
                        error_code=None,
                        updated_at_utc=now.to_unix_seconds(),
                    )
                )
                return None
            if row.status == "claimed":
                return None
            if row.attempt_count >= MAX_PART_ATTEMPTS:
                await session.execute(
                    update(ReportDeliveryRow)
                    .where(ReportDeliveryRow.id == delivery_id)
                    .values(
                        status="failed_unknown",
                        error_code="part_retry_exhausted",
                        updated_at_utc=now.to_unix_seconds(),
                    )
                )
                return None
            claimed = await session.scalar(
                update(ReportDeliveryPartRow)
                .where(
                    ReportDeliveryPartRow.id == row.id,
                    ReportDeliveryPartRow.status.in_(("pending", "failed_unknown")),
                )
                .values(
                    status="claimed",
                    attempt_count=ReportDeliveryPartRow.attempt_count + 1,
                    claimed_at_utc=now.to_unix_seconds(),
                )
                .returning(ReportDeliveryPartRow.id)
            )
            if claimed is None:
                return None
            return ReportPartWork(
                id=row.id,
                delivery_id=delivery_id,
                ordinal=row.ordinal,
                part_type=cast(
                    Literal["text", "current_week_chart", "history_chart"], row.part_type
                ),
                attempt_count=row.attempt_count + 1,
            )

    async def mark_part_sent(
        self,
        part: ReportPartWork,
        *,
        telegram_message_id: int,
        now: UtcInstant,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(ReportDeliveryPartRow)
                .where(
                    ReportDeliveryPartRow.id == part.id,
                    ReportDeliveryPartRow.status == "claimed",
                )
                .values(
                    status="sent",
                    sent_at_utc=now.to_unix_seconds(),
                    telegram_message_id=telegram_message_id,
                    error_code=None,
                )
            )

    async def mark_part_failed_unknown(
        self,
        part: ReportPartWork,
        *,
        error_code: str,
        now: UtcInstant,
    ) -> None:
        terminal = part.attempt_count >= MAX_PART_ATTEMPTS
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(ReportDeliveryPartRow)
                .where(
                    ReportDeliveryPartRow.id == part.id,
                    ReportDeliveryPartRow.status == "claimed",
                )
                .values(status="failed_unknown", error_code=error_code[:100])
            )
            await session.execute(
                update(ReportDeliveryRow)
                .where(ReportDeliveryRow.id == part.delivery_id)
                .values(
                    status="failed_unknown" if terminal else "pending",
                    error_code=("part_retry_exhausted" if terminal else error_code[:100]),
                    updated_at_utc=now.to_unix_seconds(),
                )
            )

    async def mark_generation_failed(
        self,
        work: ReportDeliveryWork,
        *,
        error_code: str,
        now: UtcInstant,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(ReportDeliveryRow)
                .where(
                    ReportDeliveryRow.id == work.id,
                    ReportDeliveryRow.status == "claimed",
                )
                .values(
                    status="failed",
                    error_code=error_code[:100],
                    updated_at_utc=now.to_unix_seconds(),
                )
            )

    async def delivery_status(self, delivery_id: int) -> str | None:
        async with self._session_factory() as session:
            value = await session.scalar(
                select(ReportDeliveryRow.status).where(ReportDeliveryRow.id == delivery_id)
            )
            return None if value is None else str(value)

    @staticmethod
    async def _insert_delivery(
        session: AsyncSession,
        *,
        user_id: int,
        report_type: ReportType,
        period_start: UtcInstant,
        period_end: UtcInstant,
        is_partial: bool,
        now: UtcInstant,
    ) -> None:
        await session.execute(
            sqlite_insert(ReportDeliveryRow)
            .values(
                user_id=user_id,
                report_type=report_type.value,
                period_start_utc=period_start.to_unix_seconds(),
                period_end_utc=period_end.to_unix_seconds(),
                is_partial=is_partial,
                status="pending",
                attempt_count=0,
                created_at_utc=now.to_unix_seconds(),
                updated_at_utc=now.to_unix_seconds(),
            )
            .on_conflict_do_nothing()
        )
