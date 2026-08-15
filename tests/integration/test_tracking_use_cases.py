"""Acceptance tests for idempotent tracking transactions."""

import asyncio
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select

from smoke_runner.application.models import (
    ChangeIntervalCommand,
    DeleteEventCommand,
    EditEventCommand,
    EventSource,
    LogEventCommand,
    SetMilestoneNotificationsCommand,
)
from smoke_runner.application.tracking import (
    RecordNotFoundError,
    TrackingService,
    WakeAlreadyExistsError,
)
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import TargetInterval
from smoke_runner.domain.timeline import MissingIntervalError
from smoke_runner.infrastructure.db.engine import create_session_factory
from smoke_runner.infrastructure.db.models import (
    IntervalChangeRow,
    MilestoneNotificationRow,
    ProcessedUpdateRow,
    SmokingSessionRow,
    UserRow,
    WakeEventRow,
)
from smoke_runner.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


@dataclass
class MutableClock:
    current: UtcInstant

    def now(self) -> UtcInstant:
        return self.current


async def seed_user(session_factory, *, telegram_id: int, with_interval: bool = True) -> int:
    activated_at = UtcInstant.from_unix_seconds(2_000_000_000)
    async with session_factory() as session, session.begin():
        user = UserRow(
            telegram_user_id=telegram_id,
            telegram_private_chat_id=telegram_id,
            role="member",
            status="active",
            timezone_name="UTC",
            activated_at_utc=activated_at.to_unix_seconds(),
            created_at_utc=activated_at.to_unix_seconds(),
            updated_at_utc=activated_at.to_unix_seconds(),
        )
        session.add(user)
        await session.flush()
        if with_interval:
            session.add(
                IntervalChangeRow(
                    user_id=user.id,
                    effective_at_utc=activated_at.to_unix_seconds(),
                    interval_seconds=3600,
                    display_unit="hour",
                    created_at_utc=activated_at.to_unix_seconds(),
                )
            )
        return user.id


def tracking_service(session_factory, clock: MutableClock) -> TrackingService:
    return TrackingService(lambda: SqlAlchemyUnitOfWork(session_factory), clock)


async def test_repeated_update_creates_one_session_and_one_pending_milestone(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    user_id = await seed_user(session_factory, telegram_id=10)
    clock = MutableClock(UtcInstant.from_unix_seconds(2_000_000_100))
    service = tracking_service(session_factory, clock)
    command = LogEventCommand(
        user_id=user_id,
        telegram_update_id=100,
        occurred_at=clock.current,
        source=EventSource.NOW,
    )

    first = await service.log_session(command)
    second = await service.log_session(command)

    assert first.applied
    assert not second.applied
    assert second.record_id is None
    async with session_factory() as session:
        session_count = await session.scalar(select(func.count()).select_from(SmokingSessionRow))
        update_count = await session.scalar(select(func.count()).select_from(ProcessedUpdateRow))
        milestones = (await session.scalars(select(MilestoneNotificationRow))).all()
    assert session_count == 1
    assert update_count == 1
    assert len(milestones) == 1
    assert milestones[0].status == "pending"
    assert milestones[0].target_at_utc == clock.current.to_unix_seconds() + 3600


async def test_concurrent_delivery_of_one_update_applies_exactly_once(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    user_id = await seed_user(session_factory, telegram_id=10)
    clock = MutableClock(UtcInstant.from_unix_seconds(2_000_000_100))
    service = tracking_service(session_factory, clock)
    command = LogEventCommand(user_id, 100, clock.current, EventSource.NOW)

    results = await asyncio.gather(
        service.log_session(command),
        service.log_session(command),
    )

    assert sum(result.applied for result in results) == 1
    async with session_factory() as session:
        session_count = await session.scalar(select(func.count()).select_from(SmokingSessionRow))
        update_count = await session.scalar(select(func.count()).select_from(ProcessedUpdateRow))
    assert session_count == 1
    assert update_count == 1


async def test_session_edit_delete_and_interval_change_recompute_pending_target(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    user_id = await seed_user(session_factory, telegram_id=10)
    clock = MutableClock(UtcInstant.from_unix_seconds(2_000_000_100))
    service = tracking_service(session_factory, clock)
    created = await service.log_session(LogEventCommand(user_id, 1, clock.current, EventSource.NOW))
    assert created.record_id is not None

    clock.current = UtcInstant.from_unix_seconds(2_000_000_200)
    edited = await service.edit_session(
        EditEventCommand(user_id, 2, created.record_id, clock.current)
    )
    changed = await service.change_interval(
        ChangeIntervalCommand(user_id, 3, TargetInterval.hours(2))
    )
    deleted = await service.delete_session(DeleteEventCommand(user_id, 4, created.record_id))

    assert edited.applied and changed.applied and deleted.applied
    async with session_factory() as session:
        row = await session.get(SmokingSessionRow, created.record_id)
        intervals = (
            await session.scalars(
                select(IntervalChangeRow).where(IntervalChangeRow.user_id == user_id)
            )
        ).all()
        pending_count = await session.scalar(
            select(func.count())
            .select_from(MilestoneNotificationRow)
            .where(MilestoneNotificationRow.status == "pending")
        )
    assert row is not None and row.deleted_at_utc == clock.current.to_unix_seconds()
    assert len(intervals) == 2
    assert intervals[-1].interval_seconds == 7200
    assert pending_count == 0


async def test_user_cannot_edit_another_users_record_and_marker_rolls_back(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    first_user = await seed_user(session_factory, telegram_id=10)
    second_user = await seed_user(session_factory, telegram_id=20)
    clock = MutableClock(UtcInstant.from_unix_seconds(2_000_000_100))
    service = tracking_service(session_factory, clock)
    created = await service.log_session(
        LogEventCommand(first_user, 1, clock.current, EventSource.NOW)
    )
    assert created.record_id is not None

    with pytest.raises(RecordNotFoundError):
        await service.edit_session(
            EditEventCommand(second_user, 99, created.record_id, clock.current)
        )

    async with session_factory() as session:
        marker = await session.get(ProcessedUpdateRow, 99)
        row = await session.get(SmokingSessionRow, created.record_id)
    assert marker is None
    assert row is not None and row.user_id == first_user


async def test_failure_after_insert_rolls_back_fact_and_update_then_retry_can_succeed(
    db_engine,
) -> None:
    session_factory = create_session_factory(db_engine)
    user_id = await seed_user(session_factory, telegram_id=10, with_interval=False)
    clock = MutableClock(UtcInstant.from_unix_seconds(2_000_000_100))
    service = tracking_service(session_factory, clock)
    command = LogEventCommand(user_id, 42, clock.current, EventSource.NOW)

    with pytest.raises(MissingIntervalError):
        await service.log_session(command)

    async with session_factory() as session, session.begin():
        assert await session.get(ProcessedUpdateRow, 42) is None
        count = await session.scalar(select(func.count()).select_from(SmokingSessionRow))
        assert count == 0
        session.add(
            IntervalChangeRow(
                user_id=user_id,
                effective_at_utc=2_000_000_000,
                interval_seconds=3600,
                display_unit="hour",
                created_at_utc=2_000_000_000,
            )
        )

    retried = await service.log_session(command)
    assert retried.applied


async def test_wake_replace_edit_delete_and_one_per_local_date(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    user_id = await seed_user(session_factory, telegram_id=10)
    clock = MutableClock(UtcInstant.from_unix_seconds(2_000_010_000))
    service = tracking_service(session_factory, clock)
    first = await service.log_wake(
        LogEventCommand(
            user_id, 1, UtcInstant.from_unix_seconds(2_000_001_000), EventSource.BACKFILL
        )
    )

    with pytest.raises(WakeAlreadyExistsError):
        await service.log_wake(
            LogEventCommand(
                user_id,
                2,
                UtcInstant.from_unix_seconds(2_000_002_000),
                EventSource.BACKFILL,
            )
        )
    replacement = await service.log_wake(
        LogEventCommand(
            user_id,
            3,
            UtcInstant.from_unix_seconds(2_000_002_000),
            EventSource.BACKFILL,
        ),
        replace_existing=True,
    )
    assert replacement.record_id is not None
    edited = await service.edit_wake(
        EditEventCommand(
            user_id,
            4,
            replacement.record_id,
            UtcInstant.from_unix_seconds(2_000_003_000),
        )
    )
    deleted = await service.delete_wake(DeleteEventCommand(user_id, 5, replacement.record_id))

    assert first.applied and edited.applied and deleted.applied
    async with session_factory() as session:
        active_count = await session.scalar(
            select(func.count())
            .select_from(WakeEventRow)
            .where(WakeEventRow.deleted_at_utc.is_(None))
        )
        failed_marker = await session.get(ProcessedUpdateRow, 2)
    assert active_count == 0
    assert failed_marker is None


async def test_notification_setting_is_idempotent_and_rebuilds_pending_milestone(
    db_engine,
) -> None:
    session_factory = create_session_factory(db_engine)
    user_id = await seed_user(session_factory, telegram_id=10)
    clock = MutableClock(UtcInstant.from_unix_seconds(2_000_000_100))
    service = tracking_service(session_factory, clock)
    await service.log_session(LogEventCommand(user_id, 1, clock.current, EventSource.NOW))

    disabled = SetMilestoneNotificationsCommand(user_id, 2, False)
    assert (await service.set_milestone_notifications(disabled)).applied
    assert not (await service.set_milestone_notifications(disabled)).applied
    async with session_factory() as session:
        user = await session.get(UserRow, user_id)
        pending_count = await session.scalar(
            select(func.count())
            .select_from(MilestoneNotificationRow)
            .where(MilestoneNotificationRow.status == "pending")
        )
    assert user is not None and not user.milestone_notifications_enabled
    assert pending_count == 0

    assert (
        await service.set_milestone_notifications(
            SetMilestoneNotificationsCommand(user_id, 3, True)
        )
    ).applied
    async with session_factory() as session:
        pending_count = await session.scalar(
            select(func.count())
            .select_from(MilestoneNotificationRow)
            .where(MilestoneNotificationRow.status == "pending")
        )
    assert pending_count == 1
