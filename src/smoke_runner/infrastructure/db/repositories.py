"""User-scoped SQLAlchemy repository adapters."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from smoke_runner.application.models import UserContext
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import (
    IntervalChange,
    IntervalUnit,
    SmokingSession,
    TargetInterval,
    WakeEvent,
)
from smoke_runner.domain.timeline import Timeline
from smoke_runner.infrastructure.db.models import (
    IntervalChangeRow,
    InviteCodeRow,
    MilestoneNotificationRow,
    ProcessedUpdateRow,
    SmokingSessionRow,
    UserRow,
    WakeEventRow,
)


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_context(self, user_id: int) -> UserContext | None:
        row = await self._session.scalar(
            select(UserRow).where(UserRow.id == user_id, UserRow.status == "active")
        )
        if row is None:
            return None
        return UserContext(
            id=row.id,
            timezone_name=row.timezone_name,
            activated_at=UtcInstant.from_unix_seconds(row.activated_at_utc),
            milestone_notifications_enabled=row.milestone_notifications_enabled,
        )


class SqlAlchemyProcessedUpdateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, update_id: int, user_id: int, processed_at: UtcInstant) -> bool:
        statement = (
            sqlite_insert(ProcessedUpdateRow)
            .values(
                telegram_update_id=update_id,
                user_id=user_id,
                outcome="processed",
                processed_at_utc=processed_at.to_unix_seconds(),
            )
            .on_conflict_do_nothing(index_elements=[ProcessedUpdateRow.telegram_update_id])
            .returning(ProcessedUpdateRow.telegram_update_id)
        )
        return (await self._session.scalar(statement)) is not None


class SqlAlchemySmokingSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self, user_id: int) -> tuple[SmokingSession, ...]:
        rows = (
            await self._session.scalars(
                select(SmokingSessionRow)
                .where(
                    SmokingSessionRow.user_id == user_id,
                    SmokingSessionRow.deleted_at_utc.is_(None),
                )
                .order_by(SmokingSessionRow.occurred_at_utc, SmokingSessionRow.id)
            )
        ).all()
        return tuple(
            SmokingSession(
                id=row.id,
                occurred_at=UtcInstant.from_unix_seconds(row.occurred_at_utc),
            )
            for row in rows
        )

    async def add(
        self,
        *,
        user_id: int,
        occurred_at: UtcInstant,
        source: str,
        update_id: int,
        now: UtcInstant,
    ) -> SmokingSession:
        row = SmokingSessionRow(
            user_id=user_id,
            occurred_at_utc=occurred_at.to_unix_seconds(),
            source=source,
            created_from_update_id=update_id,
            created_at_utc=now.to_unix_seconds(),
            updated_at_utc=now.to_unix_seconds(),
        )
        self._session.add(row)
        await self._session.flush()
        return SmokingSession(id=row.id, occurred_at=occurred_at)

    async def edit(
        self,
        *,
        user_id: int,
        record_id: int,
        occurred_at: UtcInstant,
        now: UtcInstant,
    ) -> SmokingSession | None:
        row = await self._session.scalar(
            select(SmokingSessionRow).where(
                SmokingSessionRow.id == record_id,
                SmokingSessionRow.user_id == user_id,
                SmokingSessionRow.deleted_at_utc.is_(None),
            )
        )
        if row is None:
            return None
        row.occurred_at_utc = occurred_at.to_unix_seconds()
        row.updated_at_utc = now.to_unix_seconds()
        await self._session.flush()
        return SmokingSession(id=row.id, occurred_at=occurred_at)

    async def soft_delete(self, *, user_id: int, record_id: int, now: UtcInstant) -> bool:
        result = await self._session.execute(
            update(SmokingSessionRow)
            .where(
                SmokingSessionRow.id == record_id,
                SmokingSessionRow.user_id == user_id,
                SmokingSessionRow.deleted_at_utc.is_(None),
            )
            .values(
                deleted_at_utc=now.to_unix_seconds(),
                updated_at_utc=now.to_unix_seconds(),
            )
            .returning(SmokingSessionRow.id)
        )
        return result.scalar_one_or_none() is not None


class SqlAlchemyWakeEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self, user_id: int) -> tuple[WakeEvent, ...]:
        rows = (
            await self._session.scalars(
                select(WakeEventRow)
                .where(
                    WakeEventRow.user_id == user_id,
                    WakeEventRow.deleted_at_utc.is_(None),
                )
                .order_by(WakeEventRow.occurred_at_utc, WakeEventRow.id)
            )
        ).all()
        return tuple(
            WakeEvent(id=row.id, occurred_at=UtcInstant.from_unix_seconds(row.occurred_at_utc))
            for row in rows
        )

    async def add(
        self,
        *,
        user_id: int,
        occurred_at: UtcInstant,
        source: str,
        update_id: int,
        now: UtcInstant,
    ) -> WakeEvent:
        row = WakeEventRow(
            user_id=user_id,
            occurred_at_utc=occurred_at.to_unix_seconds(),
            source=source,
            created_from_update_id=update_id,
            created_at_utc=now.to_unix_seconds(),
            updated_at_utc=now.to_unix_seconds(),
        )
        self._session.add(row)
        await self._session.flush()
        return WakeEvent(id=row.id, occurred_at=occurred_at)

    async def edit(
        self,
        *,
        user_id: int,
        record_id: int,
        occurred_at: UtcInstant,
        now: UtcInstant,
    ) -> WakeEvent | None:
        row = await self._session.scalar(
            select(WakeEventRow).where(
                WakeEventRow.id == record_id,
                WakeEventRow.user_id == user_id,
                WakeEventRow.deleted_at_utc.is_(None),
            )
        )
        if row is None:
            return None
        row.occurred_at_utc = occurred_at.to_unix_seconds()
        row.updated_at_utc = now.to_unix_seconds()
        await self._session.flush()
        return WakeEvent(id=row.id, occurred_at=occurred_at)

    async def soft_delete(self, *, user_id: int, record_id: int, now: UtcInstant) -> bool:
        result = await self._session.execute(
            update(WakeEventRow)
            .where(
                WakeEventRow.id == record_id,
                WakeEventRow.user_id == user_id,
                WakeEventRow.deleted_at_utc.is_(None),
            )
            .values(
                deleted_at_utc=now.to_unix_seconds(),
                updated_at_utc=now.to_unix_seconds(),
            )
            .returning(WakeEventRow.id)
        )
        return result.scalar_one_or_none() is not None


class SqlAlchemyIntervalChangeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self, user_id: int) -> tuple[IntervalChange, ...]:
        rows = (
            await self._session.scalars(
                select(IntervalChangeRow)
                .where(IntervalChangeRow.user_id == user_id)
                .order_by(IntervalChangeRow.effective_at_utc, IntervalChangeRow.id)
            )
        ).all()
        return tuple(_to_interval_change(row) for row in rows)

    async def add(
        self,
        *,
        user_id: int,
        effective_at: UtcInstant,
        interval: TargetInterval,
        update_id: int,
        now: UtcInstant,
    ) -> IntervalChange:
        row = IntervalChangeRow(
            user_id=user_id,
            effective_at_utc=effective_at.to_unix_seconds(),
            interval_seconds=interval.seconds,
            display_unit=interval.unit.value,
            created_from_update_id=update_id,
            created_at_utc=now.to_unix_seconds(),
        )
        self._session.add(row)
        await self._session.flush()
        return IntervalChange(id=row.id, effective_at=effective_at, interval=interval)


class SqlAlchemyMilestoneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_pending(
        self,
        *,
        user_id: int,
        timeline: Timeline,
        now: UtcInstant,
        enabled: bool,
    ) -> None:
        now_seconds = now.to_unix_seconds()
        await self._session.execute(
            update(MilestoneNotificationRow)
            .where(
                MilestoneNotificationRow.user_id == user_id,
                MilestoneNotificationRow.status == "pending",
            )
            .values(status="superseded", updated_at_utc=now_seconds)
        )
        if (
            not enabled
            or timeline.current_target_at is None
            or timeline.current_target_at <= now
            or not timeline.sessions
        ):
            return
        self._session.add(
            MilestoneNotificationRow(
                user_id=user_id,
                basis_session_id=timeline.sessions[-1].session.id,
                target_at_utc=timeline.current_target_at.to_unix_seconds(),
                status="pending",
                created_at_utc=now_seconds,
                updated_at_utc=now_seconds,
            )
        )
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class RedeemedUser:
    id: int
    telegram_user_id: int


class SqlAlchemyInviteRedemption:
    """Atomic one-time invite redemption used by auth in the next stage."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def redeem(
        self,
        *,
        code_digest: str,
        telegram_user_id: int,
        telegram_private_chat_id: int,
        timezone_name: str,
        now: UtcInstant,
    ) -> RedeemedUser | None:
        now_seconds = now.to_unix_seconds()
        claim = (
            update(InviteCodeRow)
            .where(
                InviteCodeRow.code_digest == code_digest,
                InviteCodeRow.expires_at_utc > now_seconds,
                InviteCodeRow.redeemed_at_utc.is_(None),
                InviteCodeRow.revoked_at_utc.is_(None),
            )
            .values(redeemed_at_utc=now_seconds)
            .returning(InviteCodeRow.id)
        )
        invite_id = (await self._session.execute(claim)).scalar_one_or_none()
        if invite_id is None:
            return None

        user = UserRow(
            telegram_user_id=telegram_user_id,
            telegram_private_chat_id=telegram_private_chat_id,
            role="member",
            status="active",
            timezone_name=timezone_name,
            milestone_notifications_enabled=True,
            ai_commentary_enabled=False,
            activated_at_utc=now_seconds,
            created_at_utc=now_seconds,
            updated_at_utc=now_seconds,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.execute(
            update(InviteCodeRow)
            .where(InviteCodeRow.id == invite_id)
            .values(redeemed_by_user_id=user.id)
        )
        self._session.add(
            IntervalChangeRow(
                user_id=user.id,
                effective_at_utc=now_seconds,
                interval_seconds=TargetInterval.hours(1).seconds,
                display_unit=IntervalUnit.HOUR.value,
                created_at_utc=now_seconds,
            )
        )
        await self._session.flush()
        return RedeemedUser(id=user.id, telegram_user_id=user.telegram_user_id)


def _to_interval_change(row: IntervalChangeRow) -> IntervalChange:
    unit = IntervalUnit(row.display_unit)
    divisor = 60 * 60 if unit is IntervalUnit.HOUR else 24 * 60 * 60
    return IntervalChange(
        id=row.id,
        effective_at=UtcInstant.from_unix_seconds(row.effective_at_utc),
        interval=TargetInterval(count=row.interval_seconds // divisor, unit=unit),
    )
