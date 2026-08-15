"""Durable SQLite-backed milestone and active-dashboard scheduler."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import BufferedInputFile
from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from smoke_runner.application.security import AuthenticatedUser
from smoke_runner.domain.clock import Clock, UtcInstant
from smoke_runner.domain.report_models import DailyReportSnapshot, WeeklyReportSnapshot
from smoke_runner.domain.timeline import build_timeline
from smoke_runner.infrastructure.db.engine import SessionFactory
from smoke_runner.infrastructure.db.gateway import DatabaseGateway
from smoke_runner.infrastructure.db.models import (
    DashboardStateRow,
    MilestoneNotificationRow,
    RuntimeStateRow,
    SmokingSessionRow,
    UserRow,
)
from smoke_runner.infrastructure.report_rendering import (
    deserialize_report_snapshot,
    render_current_week_chart,
    render_history_chart,
    render_report_text,
)
from smoke_runner.infrastructure.reports import (
    ReportBuilder,
    ReportDeliveryStore,
    ReportDeliveryWork,
    ReportPartWork,
)
from smoke_runner.infrastructure.telegram.keyboards import dashboard_keyboard
from smoke_runner.infrastructure.telegram.presenters import render_dashboard
from smoke_runner.infrastructure.telegram.screens import (
    DASHBOARD_REFRESH_INTERVAL,
    ScreenLocks,
    next_dashboard_refresh,
)

LOGGER = logging.getLogger(__name__)
MILESTONE_CATCHUP_WINDOW = timedelta(minutes=15)
MAX_SCHEDULER_SLEEP = timedelta(seconds=30)
CLAIM_RESERVATION_PADDING = timedelta(seconds=1)
MILESTONE_TEXT = "Молодец, ты выдержал интервал! Теперь можешь покурить спокойно 🙂"


@dataclass(frozen=True, slots=True)
class MilestoneWork:
    id: int
    user_id: int
    telegram_chat_id: int
    target_at: UtcInstant


@dataclass(frozen=True, slots=True)
class DashboardWork:
    user: AuthenticatedUser
    telegram_chat_id: int
    telegram_message_id: int
    reserved_next_refresh_at: UtcInstant


class SchedulerStore:
    """Short transaction-per-operation durable scheduler state."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def recover(self, now: UtcInstant) -> None:
        """Apply startup-only at-most-once and catch-up recovery rules."""
        now_seconds = now.to_unix_seconds()
        stale_before = (now - MILESTONE_CATCHUP_WINDOW).to_unix_seconds()
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(MilestoneNotificationRow)
                .where(MilestoneNotificationRow.status == "claimed")
                .values(
                    status="failed_unknown",
                    error_code="restart_after_claim",
                    updated_at_utc=now_seconds,
                )
            )
            await session.execute(
                update(MilestoneNotificationRow)
                .where(
                    MilestoneNotificationRow.status == "pending",
                    MilestoneNotificationRow.target_at_utc < stale_before,
                )
                .values(status="skipped_stale", updated_at_utc=now_seconds)
            )
            await self._expire_dashboard_windows(session, now_seconds)
        await self.heartbeat(now)

    async def heartbeat(self, now: UtcInstant) -> None:
        statement = (
            sqlite_insert(RuntimeStateRow)
            .values(
                key="scheduler_heartbeat_at_utc",
                value=str(now.to_unix_seconds()),
                updated_at_utc=now.to_unix_seconds(),
            )
            .on_conflict_do_update(
                index_elements=[RuntimeStateRow.key],
                set_={
                    "value": str(now.to_unix_seconds()),
                    "updated_at_utc": now.to_unix_seconds(),
                },
            )
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(statement)

    async def claim_due_milestone(self, now: UtcInstant) -> MilestoneWork | None:
        now_seconds = now.to_unix_seconds()
        stale_before = (now - MILESTONE_CATCHUP_WINDOW).to_unix_seconds()
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(MilestoneNotificationRow)
                .where(
                    MilestoneNotificationRow.status == "pending",
                    MilestoneNotificationRow.target_at_utc < stale_before,
                )
                .values(status="skipped_stale", updated_at_utc=now_seconds)
            )
            candidate = (
                await session.execute(
                    select(MilestoneNotificationRow, UserRow.telegram_private_chat_id)
                    .join(UserRow, UserRow.id == MilestoneNotificationRow.user_id)
                    .join(
                        SmokingSessionRow,
                        SmokingSessionRow.id == MilestoneNotificationRow.basis_session_id,
                    )
                    .where(
                        MilestoneNotificationRow.status == "pending",
                        MilestoneNotificationRow.target_at_utc <= now_seconds,
                        MilestoneNotificationRow.target_at_utc >= stale_before,
                        UserRow.status == "active",
                        UserRow.milestone_notifications_enabled.is_(True),
                        SmokingSessionRow.deleted_at_utc.is_(None),
                    )
                    .order_by(
                        MilestoneNotificationRow.target_at_utc,
                        MilestoneNotificationRow.id,
                    )
                    .limit(1)
                )
            ).first()
            if candidate is None:
                return None
            row, chat_id = candidate
            claimed = await session.scalar(
                update(MilestoneNotificationRow)
                .where(
                    MilestoneNotificationRow.id == row.id,
                    MilestoneNotificationRow.status == "pending",
                )
                .values(
                    status="claimed",
                    claimed_at_utc=now_seconds,
                    updated_at_utc=now_seconds,
                )
                .returning(MilestoneNotificationRow.id)
            )
            if claimed is None:
                return None
            return MilestoneWork(
                id=row.id,
                user_id=row.user_id,
                telegram_chat_id=chat_id,
                target_at=UtcInstant.from_unix_seconds(row.target_at_utc),
            )

    async def milestone_claim_is_current(self, work: MilestoneWork) -> bool:
        async with self._session_factory() as session:
            current = await session.scalar(
                select(MilestoneNotificationRow.id)
                .join(UserRow, UserRow.id == MilestoneNotificationRow.user_id)
                .join(
                    SmokingSessionRow,
                    SmokingSessionRow.id == MilestoneNotificationRow.basis_session_id,
                )
                .where(
                    MilestoneNotificationRow.id == work.id,
                    MilestoneNotificationRow.status == "claimed",
                    UserRow.status == "active",
                    UserRow.milestone_notifications_enabled.is_(True),
                    SmokingSessionRow.deleted_at_utc.is_(None),
                )
            )
            return current is not None

    async def mark_milestone_sent(
        self,
        work: MilestoneWork,
        *,
        telegram_message_id: int,
        now: UtcInstant,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(MilestoneNotificationRow)
                .where(
                    MilestoneNotificationRow.id == work.id,
                    MilestoneNotificationRow.status == "claimed",
                )
                .values(
                    status="sent",
                    sent_at_utc=now.to_unix_seconds(),
                    telegram_message_id=telegram_message_id,
                    updated_at_utc=now.to_unix_seconds(),
                )
            )

    async def mark_milestone_failed_unknown(
        self,
        work: MilestoneWork,
        *,
        error_code: str,
        now: UtcInstant,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(MilestoneNotificationRow)
                .where(
                    MilestoneNotificationRow.id == work.id,
                    MilestoneNotificationRow.status == "claimed",
                )
                .values(
                    status="failed_unknown",
                    error_code=error_code[:100],
                    updated_at_utc=now.to_unix_seconds(),
                )
            )

    async def make_dashboard_due(self, user_id: int, now: UtcInstant) -> None:
        now_seconds = now.to_unix_seconds()
        async with self._session_factory() as session, session.begin():
            row = await session.get(DashboardStateRow, user_id)
            if (
                row is not None
                and row.screen_kind == "dashboard"
                and row.active_until_utc is not None
                and row.active_until_utc > now_seconds
                and row.telegram_message_id is not None
            ):
                row.next_refresh_at_utc = min(row.next_refresh_at_utc or now_seconds, now_seconds)

    async def claim_due_dashboard(self, now: UtcInstant) -> DashboardWork | None:
        now_seconds = now.to_unix_seconds()
        async with self._session_factory() as session, session.begin():
            await self._expire_dashboard_windows(session, now_seconds)
            candidate = (
                await session.execute(
                    select(DashboardStateRow, UserRow)
                    .join(UserRow, UserRow.id == DashboardStateRow.user_id)
                    .where(
                        DashboardStateRow.screen_kind == "dashboard",
                        DashboardStateRow.telegram_message_id.is_not(None),
                        DashboardStateRow.active_until_utc > now_seconds,
                        DashboardStateRow.next_refresh_at_utc <= now_seconds,
                        UserRow.status == "active",
                    )
                    .order_by(DashboardStateRow.next_refresh_at_utc, DashboardStateRow.user_id)
                    .limit(1)
                )
            ).first()
            if candidate is None:
                return None
            state, user = candidate
            assert state.active_until_utc is not None
            assert state.next_refresh_at_utc is not None
            assert state.telegram_message_id is not None
            reserved_seconds = min(
                (now + DASHBOARD_REFRESH_INTERVAL + CLAIM_RESERVATION_PADDING).to_unix_seconds(),
                state.active_until_utc,
            )
            claimed = await session.scalar(
                update(DashboardStateRow)
                .where(
                    DashboardStateRow.user_id == state.user_id,
                    DashboardStateRow.screen_kind == "dashboard",
                    DashboardStateRow.next_refresh_at_utc == state.next_refresh_at_utc,
                )
                .values(
                    next_refresh_at_utc=reserved_seconds,
                    updated_at_utc=now_seconds,
                )
                .returning(DashboardStateRow.user_id)
            )
            if claimed is None:
                return None
            return DashboardWork(
                user=AuthenticatedUser(
                    id=user.id,
                    telegram_user_id=user.telegram_user_id,
                    telegram_private_chat_id=user.telegram_private_chat_id,
                    role=user.role,
                    timezone_name=user.timezone_name,
                ),
                telegram_chat_id=state.telegram_chat_id,
                telegram_message_id=state.telegram_message_id,
                reserved_next_refresh_at=UtcInstant.from_unix_seconds(reserved_seconds),
            )

    async def dashboard_claim_is_current(self, work: DashboardWork, now: UtcInstant) -> bool:
        async with self._session_factory() as session:
            current = await session.scalar(
                select(DashboardStateRow.user_id).where(
                    DashboardStateRow.user_id == work.user.id,
                    DashboardStateRow.screen_kind == "dashboard",
                    DashboardStateRow.telegram_chat_id == work.telegram_chat_id,
                    DashboardStateRow.telegram_message_id == work.telegram_message_id,
                    DashboardStateRow.active_until_utc > now.to_unix_seconds(),
                    DashboardStateRow.next_refresh_at_utc
                    == work.reserved_next_refresh_at.to_unix_seconds(),
                )
            )
            return current is not None

    async def complete_dashboard_refresh(
        self,
        work: DashboardWork,
        *,
        now: UtcInstant,
        target_at: UtcInstant | None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(DashboardStateRow, work.user.id)
            if (
                row is None
                or row.screen_kind != "dashboard"
                or row.next_refresh_at_utc != work.reserved_next_refresh_at.to_unix_seconds()
                or row.active_until_utc is None
                or row.active_until_utc <= now.to_unix_seconds()
            ):
                return
            active_until = UtcInstant.from_unix_seconds(row.active_until_utc)
            row.next_refresh_at_utc = next_dashboard_refresh(
                now, active_until, target_at
            ).to_unix_seconds()
            row.updated_at_utc = now.to_unix_seconds()

    async def disable_dashboard(self, work: DashboardWork, now: UtcInstant) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(DashboardStateRow)
                .where(
                    DashboardStateRow.user_id == work.user.id,
                    DashboardStateRow.screen_kind == "dashboard",
                    DashboardStateRow.telegram_message_id == work.telegram_message_id,
                )
                .values(
                    telegram_message_id=None,
                    active_until_utc=None,
                    next_refresh_at_utc=None,
                    updated_at_utc=now.to_unix_seconds(),
                )
            )

    async def next_due_at(self, now: UtcInstant) -> UtcInstant | None:
        now_seconds = now.to_unix_seconds()
        async with self._session_factory() as session:
            milestone_due = await session.scalar(
                select(func.min(MilestoneNotificationRow.target_at_utc))
                .join(UserRow, UserRow.id == MilestoneNotificationRow.user_id)
                .join(
                    SmokingSessionRow,
                    SmokingSessionRow.id == MilestoneNotificationRow.basis_session_id,
                )
                .where(
                    MilestoneNotificationRow.status == "pending",
                    UserRow.status == "active",
                    UserRow.milestone_notifications_enabled.is_(True),
                    SmokingSessionRow.deleted_at_utc.is_(None),
                )
            )
            dashboard_due = await session.scalar(
                select(func.min(DashboardStateRow.next_refresh_at_utc))
                .join(UserRow, UserRow.id == DashboardStateRow.user_id)
                .where(
                    DashboardStateRow.screen_kind == "dashboard",
                    DashboardStateRow.active_until_utc > now_seconds,
                    DashboardStateRow.next_refresh_at_utc.is_not(None),
                    UserRow.status == "active",
                )
            )
        due_values = [value for value in (milestone_due, dashboard_due) if value is not None]
        if not due_values:
            return None
        return UtcInstant.from_unix_seconds(min(due_values))

    @staticmethod
    async def _expire_dashboard_windows(session: AsyncSession, now_seconds: int) -> None:
        await session.execute(
            update(DashboardStateRow)
            .where(
                DashboardStateRow.active_until_utc.is_not(None),
                DashboardStateRow.active_until_utc <= now_seconds,
            )
            .values(active_until_utc=None, next_refresh_at_utc=None)
        )


class DurableScheduler:
    def __init__(
        self,
        *,
        store: SchedulerStore,
        gateway: DatabaseGateway,
        bot: Bot,
        clock: Clock,
        wakeup: asyncio.Event,
        screen_locks: ScreenLocks,
        report_store: ReportDeliveryStore | None = None,
        report_builder: ReportBuilder | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._bot = bot
        self._clock = clock
        self._wakeup = wakeup
        self._screen_locks = screen_locks
        self._report_store = report_store
        self._report_builder = report_builder
        self._stopping = False
        self._recovered = False

    def stop(self) -> None:
        self._stopping = True
        self._wakeup.set()

    async def recover(self) -> None:
        now = self._clock.now()
        await self._store.recover(now)
        if self._report_store is not None:
            await self._report_store.recover(now)
        self._recovered = True

    async def tick(self) -> None:
        if self._report_store is not None:
            await self._report_store.ensure_due_deliveries(self._clock.now())
        while milestone_work := await self._store.claim_due_milestone(self._clock.now()):
            await self._deliver_milestone(milestone_work)
        if self._report_store is not None and self._report_builder is not None:
            while report_work := await self._report_store.claim_due(self._clock.now()):
                await self._deliver_report(report_work)
        while dashboard_work := await self._store.claim_due_dashboard(self._clock.now()):
            await self._refresh_dashboard(dashboard_work)
        await self._store.heartbeat(self._clock.now())

    async def run(self) -> None:
        if not self._recovered:
            await self.recover()
        while not self._stopping:
            self._wakeup.clear()
            await self.tick()
            now = self._clock.now()
            next_due = await self._store.next_due_at(now)
            if self._report_store is not None:
                report_due = await self._report_store.next_boundary(now)
                if report_due is not None and (next_due is None or report_due < next_due):
                    next_due = report_due
            delay = MAX_SCHEDULER_SLEEP.total_seconds()
            if next_due is not None:
                delay = min(delay, max(0.0, (next_due - now).total_seconds()))
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _deliver_milestone(self, work: MilestoneWork) -> None:
        if not await self._store.milestone_claim_is_current(work):
            return
        try:
            message = await self._bot.send_message(
                chat_id=work.telegram_chat_id,
                text=MILESTONE_TEXT,
            )
        except TelegramAPIError as error:
            await self._store.mark_milestone_failed_unknown(
                work,
                error_code=type(error).__name__,
                now=self._clock.now(),
            )
            LOGGER.warning("Milestone delivery failed", extra={"milestone_id": work.id})
            return
        now = self._clock.now()
        await self._store.mark_milestone_sent(
            work,
            telegram_message_id=message.message_id,
            now=now,
        )
        await self._store.make_dashboard_due(work.user_id, now)

    async def _refresh_dashboard(self, work: DashboardWork) -> None:
        async with self._screen_locks.for_user(work.user.id):
            now = self._clock.now()
            if not await self._store.dashboard_claim_is_current(work, now):
                return
            facts = await self._gateway.dashboard_facts(work.user.id)
            if facts is None:
                await self._store.disable_dashboard(work, now)
                return
            timeline = build_timeline(list(facts.sessions), list(facts.intervals))
            try:
                await self._bot.edit_message_text(
                    chat_id=work.telegram_chat_id,
                    message_id=work.telegram_message_id,
                    text=render_dashboard(facts, now),
                    reply_markup=dashboard_keyboard(),
                )
            except TelegramBadRequest as error:
                if "message is not modified" not in error.message.lower():
                    await self._store.disable_dashboard(work, now)
                    return
            except TelegramAPIError:
                LOGGER.warning(
                    "Dashboard refresh failed",
                    extra={"user_id": work.user.id},
                )
            await self._store.complete_dashboard_refresh(
                work,
                now=now,
                target_at=timeline.current_target_at,
            )

    async def _deliver_report(self, work: ReportDeliveryWork) -> None:
        assert self._report_store is not None
        assert self._report_builder is not None
        snapshot_json = work.snapshot_json
        if snapshot_json is None:
            try:
                generated = await self._report_builder.build_delivery(
                    work,
                    generated_at=self._clock.now(),
                )
                snapshot_json = await self._report_store.save_generated(
                    work,
                    generated,
                    now=self._clock.now(),
                )
            except Exception as error:
                await self._report_store.mark_generation_failed(
                    work,
                    error_code=type(error).__name__,
                    now=self._clock.now(),
                )
                LOGGER.exception("Report generation failed", extra={"delivery_id": work.id})
                return
        try:
            payload = json.loads(snapshot_json)
            snapshot = deserialize_report_snapshot(payload)
            commentary = payload.get("commentary")
            if commentary is not None and not isinstance(commentary, str):
                commentary = None
        except TypeError, ValueError, KeyError:
            await self._report_store.mark_generation_failed(
                work,
                error_code="invalid_snapshot",
                now=self._clock.now(),
            )
            LOGGER.exception("Stored report snapshot is invalid", extra={"delivery_id": work.id})
            return

        while part := await self._report_store.claim_next_part(work.id, now=self._clock.now()):
            if not await self._send_report_part(work, part, snapshot, commentary):
                return

    async def _send_report_part(
        self,
        work: ReportDeliveryWork,
        part: ReportPartWork,
        snapshot: DailyReportSnapshot | WeeklyReportSnapshot,
        commentary: str | None,
    ) -> bool:
        assert self._report_store is not None
        try:
            if part.part_type == "text":
                message = await self._bot.send_message(
                    chat_id=work.telegram_chat_id,
                    text=render_report_text(snapshot, commentary=commentary),
                )
            else:
                if not isinstance(snapshot, WeeklyReportSnapshot):
                    raise ValueError("A daily report cannot contain chart parts")
                if part.part_type == "current_week_chart":
                    image = await asyncio.to_thread(render_current_week_chart, snapshot)
                    filename = "week-by-day.png"
                    caption = "Неделя по дням"
                else:
                    image = await asyncio.to_thread(render_history_chart, snapshot)
                    filename = "history-by-week.png"
                    caption = "Весь период по неделям"
                message = await self._bot.send_photo(
                    chat_id=work.telegram_chat_id,
                    photo=BufferedInputFile(image, filename=filename),
                    caption=caption,
                )
        except TelegramAPIError as error:
            await self._report_store.mark_part_failed_unknown(
                part,
                error_code=type(error).__name__,
                now=self._clock.now(),
            )
            LOGGER.warning(
                "Report part delivery failed",
                extra={"delivery_id": work.id, "part_id": part.id},
            )
            return False
        await self._report_store.mark_part_sent(
            part,
            telegram_message_id=message.message_id,
            now=self._clock.now(),
        )
        return True
