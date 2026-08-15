"""Durable scheduler milestone and active-dashboard acceptance tests."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from smoke_runner.application.models import EventSource, LogEventCommand
from smoke_runner.application.tracking import TrackingService
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.infrastructure.db.engine import create_session_factory
from smoke_runner.infrastructure.db.gateway import DatabaseGateway
from smoke_runner.infrastructure.db.models import (
    MilestoneNotificationRow,
    RuntimeStateRow,
    SmokingSessionRow,
)
from smoke_runner.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from smoke_runner.infrastructure.scheduler import DurableScheduler, SchedulerStore
from smoke_runner.infrastructure.telegram.keyboards import dashboard_keyboard
from smoke_runner.infrastructure.telegram.screens import ScreenLocks, ScreenManager

START = UtcInstant.from_unix_seconds(2_000_000_000)


@dataclass
class MutableClock:
    value: UtcInstant = START

    def now(self) -> UtcInstant:
        return self.value


class FakeBot:
    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.edited_texts: list[str] = []
        self.next_message_id = 100

    async def send_message(self, **kwargs):
        self.sent_texts.append(str(kwargs["text"]))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id)

    async def edit_message_text(self, **kwargs):
        self.edited_texts.append(str(kwargs["text"]))


async def make_scheduler(db_engine, clock: MutableClock):
    session_factory = create_session_factory(db_engine)
    gateway = DatabaseGateway(session_factory)
    admin = await gateway.bootstrap_admin(
        telegram_user_id=1,
        timezone_name="UTC",
        now=clock.now(),
    )
    bot = FakeBot()
    locks = ScreenLocks()
    wakeup = asyncio.Event()
    store = SchedulerStore(session_factory)
    scheduler = DurableScheduler(
        store=store,
        gateway=gateway,
        bot=bot,  # type: ignore[arg-type]
        clock=clock,
        wakeup=wakeup,
        screen_locks=locks,
    )
    return session_factory, gateway, admin, bot, locks, wakeup, store, scheduler


async def test_early_session_supersedes_old_milestone_and_only_new_target_sends(
    db_engine,
) -> None:
    clock = MutableClock()
    session_factory, _, admin, bot, _, _, _, scheduler = await make_scheduler(db_engine, clock)
    tracking = TrackingService(lambda: SqlAlchemyUnitOfWork(session_factory), clock)
    first = clock.now()
    await tracking.log_session(LogEventCommand(admin.id, 1, first, EventSource.NOW))
    clock.value = first + timedelta(minutes=30)
    await tracking.log_session(LogEventCommand(admin.id, 2, clock.now(), EventSource.NOW))

    async with session_factory() as session:
        milestones = (
            await session.scalars(
                select(MilestoneNotificationRow).order_by(MilestoneNotificationRow.id)
            )
        ).all()
    assert [row.status for row in milestones] == ["superseded", "pending"]
    assert milestones[1].target_at_utc == (first + timedelta(minutes=90)).to_unix_seconds()

    clock.value = first + timedelta(hours=1)
    await scheduler.tick()
    assert bot.sent_texts == []
    clock.value = first + timedelta(minutes=90)
    await scheduler.tick()
    await scheduler.tick()
    assert len(bot.sent_texts) == 1


async def test_small_lateness_keeps_target_grid_in_persisted_milestone(db_engine) -> None:
    clock = MutableClock()
    session_factory, _, admin, _, _, _, _, _ = await make_scheduler(db_engine, clock)
    tracking = TrackingService(lambda: SqlAlchemyUnitOfWork(session_factory), clock)
    first = clock.now()
    await tracking.log_session(LogEventCommand(admin.id, 10, first, EventSource.NOW))
    clock.value = first + timedelta(hours=1, minutes=5)
    await tracking.log_session(LogEventCommand(admin.id, 11, clock.now(), EventSource.NOW))

    async with session_factory() as session:
        pending = await session.scalar(
            select(MilestoneNotificationRow).where(MilestoneNotificationRow.status == "pending")
        )
    assert pending is not None
    assert pending.target_at_utc == (first + timedelta(hours=2)).to_unix_seconds()


async def test_recovery_skips_stale_and_never_retries_claimed_milestones(db_engine) -> None:
    clock = MutableClock()
    session_factory, _, admin, bot, _, _, _, scheduler = await make_scheduler(db_engine, clock)
    async with session_factory() as session, session.begin():
        smoking = SmokingSessionRow(
            user_id=admin.id,
            occurred_at_utc=(clock.now() - timedelta(hours=2)).to_unix_seconds(),
            source="now",
            created_at_utc=clock.now().to_unix_seconds(),
            updated_at_utc=clock.now().to_unix_seconds(),
        )
        session.add(smoking)
        await session.flush()
        session.add_all(
            [
                MilestoneNotificationRow(
                    user_id=admin.id,
                    basis_session_id=smoking.id,
                    target_at_utc=(clock.now() - timedelta(minutes=16)).to_unix_seconds(),
                    status="pending",
                    created_at_utc=clock.now().to_unix_seconds(),
                    updated_at_utc=clock.now().to_unix_seconds(),
                ),
                MilestoneNotificationRow(
                    user_id=admin.id,
                    basis_session_id=smoking.id,
                    target_at_utc=clock.now().to_unix_seconds(),
                    status="claimed",
                    claimed_at_utc=clock.now().to_unix_seconds(),
                    created_at_utc=clock.now().to_unix_seconds(),
                    updated_at_utc=clock.now().to_unix_seconds(),
                ),
            ]
        )

    await scheduler.recover()
    await scheduler.tick()

    async with session_factory() as session:
        statuses = (
            await session.scalars(
                select(MilestoneNotificationRow.status).order_by(MilestoneNotificationRow.id)
            )
        ).all()
        heartbeat = await session.get(RuntimeStateRow, "scheduler_heartbeat_at_utc")
    assert statuses == ["skipped_stale", "failed_unknown"]
    assert bot.sent_texts == []
    assert heartbeat is not None


async def test_recovery_delivers_pending_milestone_within_fifteen_minute_window(
    db_engine,
) -> None:
    clock = MutableClock()
    session_factory, _, admin, bot, _, _, _, scheduler = await make_scheduler(db_engine, clock)
    tracking = TrackingService(lambda: SqlAlchemyUnitOfWork(session_factory), clock)
    first = clock.now()
    await tracking.log_session(LogEventCommand(admin.id, 20, first, EventSource.NOW))
    clock.value = first + timedelta(hours=1, minutes=10)

    await scheduler.recover()
    await scheduler.tick()

    assert len(bot.sent_texts) == 1
    assert "Теперь можешь покурить спокойно" in bot.sent_texts[0]


async def test_new_session_supersedes_even_an_already_claimed_milestone(db_engine) -> None:
    clock = MutableClock()
    session_factory, _, admin, bot, _, _, store, scheduler = await make_scheduler(db_engine, clock)
    tracking = TrackingService(lambda: SqlAlchemyUnitOfWork(session_factory), clock)
    first = clock.now()
    await tracking.log_session(LogEventCommand(admin.id, 30, first, EventSource.NOW))
    clock.value = first + timedelta(hours=1)
    claimed = await store.claim_due_milestone(clock.now())
    assert claimed is not None

    await tracking.log_session(LogEventCommand(admin.id, 31, clock.now(), EventSource.NOW))

    assert not await store.milestone_claim_is_current(claimed)
    await scheduler.tick()
    assert bot.sent_texts == []


async def test_active_dashboard_refreshes_at_target_then_stops_on_history(db_engine) -> None:
    clock = MutableClock()
    _, gateway, admin, bot, locks, wakeup, _, scheduler = await make_scheduler(db_engine, clock)
    screens = ScreenManager(gateway, clock, locks=locks, scheduler_wakeup=wakeup)
    target = clock.now() + timedelta(minutes=2)
    await screens.show(
        bot=bot,  # type: ignore[arg-type]
        user=admin,
        chat_id=1,
        text="dashboard",
        reply_markup=dashboard_keyboard(),
        screen_kind="dashboard",
        target_at=target,
    )
    state = await gateway.get_dashboard_state(admin.id)
    assert state is not None
    assert state.next_refresh_at == target

    clock.value = target
    await scheduler.tick()
    assert len(bot.edited_texts) == 1

    await screens.show(
        bot=bot,  # type: ignore[arg-type]
        user=admin,
        chat_id=1,
        text="history",
        reply_markup=dashboard_keyboard(),
        screen_kind="history",
    )
    clock.value = target + timedelta(minutes=10)
    await scheduler.tick()
    assert len(bot.edited_texts) == 2  # one scheduler edit and one foreground history edit
    state = await gateway.get_dashboard_state(admin.id)
    assert state is not None
    assert state.screen_kind == "history"
    assert state.active_until is None


async def test_one_dashboard_opening_has_at_most_five_background_edits(db_engine) -> None:
    clock = MutableClock()
    _, gateway, admin, bot, locks, wakeup, _, scheduler = await make_scheduler(db_engine, clock)
    screens = ScreenManager(gateway, clock, locks=locks, scheduler_wakeup=wakeup)
    opened_at = clock.now()
    await screens.show(
        bot=bot,  # type: ignore[arg-type]
        user=admin,
        chat_id=1,
        text="dashboard",
        reply_markup=dashboard_keyboard(),
        screen_kind="dashboard",
    )
    for minutes in (5, 10, 15, 20, 25, 30, 35):
        clock.value = opened_at + timedelta(minutes=minutes)
        await scheduler.tick()

    assert len(bot.edited_texts) == 5
    state = await gateway.get_dashboard_state(admin.id)
    assert state is not None
    assert state.active_until is None
    assert state.next_refresh_at is None


async def test_scheduler_run_stops_gracefully_when_woken(db_engine) -> None:
    clock = MutableClock()
    _, _, _, _, _, _, _, scheduler = await make_scheduler(db_engine, clock)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0)
    scheduler.stop()
    await asyncio.wait_for(task, timeout=1)
