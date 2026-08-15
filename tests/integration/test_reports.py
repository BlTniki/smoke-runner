"""Automatic reports, ordering and crash recovery acceptance tests."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from smoke_runner.application.models import EventSource, LogEventCommand
from smoke_runner.application.tracking import TrackingService
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.infrastructure.ai_commentary import DisabledReportCommentator
from smoke_runner.infrastructure.db.engine import create_session_factory
from smoke_runner.infrastructure.db.gateway import DatabaseGateway
from smoke_runner.infrastructure.db.models import ReportDeliveryPartRow, ReportDeliveryRow
from smoke_runner.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from smoke_runner.infrastructure.reports import ReportBuilder, ReportDeliveryStore
from smoke_runner.infrastructure.scheduler import DurableScheduler, SchedulerStore
from smoke_runner.infrastructure.telegram.screens import ScreenLocks


def at(day: int, hour: int = 0, minute: int = 0) -> UtcInstant:
    return UtcInstant(datetime(2026, 8, day, hour, minute, tzinfo=UTC))


@dataclass
class MutableClock:
    value: UtcInstant

    def now(self) -> UtcInstant:
        return self.value


class ReportBot:
    def __init__(self) -> None:
        self.sent_texts: list[str] = []
        self.sent_photos: list[tuple[str, bytes]] = []
        self.next_message_id = 100

    async def send_message(self, **kwargs):
        self.sent_texts.append(str(kwargs["text"]))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id)

    async def send_photo(self, **kwargs):
        photo = kwargs["photo"]
        self.sent_photos.append((str(kwargs["caption"]), photo.data))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id)

    async def edit_message_text(self, **kwargs):
        del kwargs


async def make_report_scheduler(db_engine, *, activated_at: UtcInstant, now: UtcInstant):
    session_factory = create_session_factory(db_engine)
    gateway = DatabaseGateway(session_factory)
    user = await gateway.bootstrap_admin(
        telegram_user_id=1,
        timezone_name="UTC",
        now=activated_at,
    )
    clock = MutableClock(now)
    bot = ReportBot()
    report_store = ReportDeliveryStore(session_factory)
    scheduler = DurableScheduler(
        store=SchedulerStore(session_factory),
        gateway=gateway,
        bot=bot,  # type: ignore[arg-type]
        clock=clock,
        wakeup=asyncio.Event(),
        screen_locks=ScreenLocks(),
        report_store=report_store,
        report_builder=ReportBuilder(gateway, DisabledReportCommentator()),
    )
    tracking = TrackingService(lambda: SqlAlchemyUnitOfWork(session_factory), clock)
    return session_factory, user, clock, bot, report_store, scheduler, tracking


async def test_daily_report_is_sent_at_nine_and_not_duplicated(db_engine) -> None:
    (
        session_factory,
        user,
        clock,
        bot,
        _,
        scheduler,
        tracking,
    ) = await make_report_scheduler(db_engine, activated_at=at(14, 8), now=at(15, 8, 59))
    await tracking.log_session(LogEventCommand(user.id, 1, at(14, 10), EventSource.BACKFILL))
    await tracking.log_session(LogEventCommand(user.id, 2, at(14, 11, 5), EventSource.BACKFILL))

    await scheduler.recover()
    await scheduler.tick()
    assert bot.sent_texts == []

    clock.value = at(15, 9)
    await scheduler.tick()
    await scheduler.tick()

    assert len(bot.sent_texts) == 1
    assert "Ежедневный отчёт · 14.08.2026" in bot.sent_texts[0]
    assert "Эпизоды: 2" in bot.sent_texts[0]
    async with session_factory() as session:
        delivery = await session.scalar(select(ReportDeliveryRow))
        parts = (await session.scalars(select(ReportDeliveryPartRow))).all()
    assert delivery is not None
    assert delivery.status == "sent"
    assert delivery.snapshot_json is not None
    assert [part.status for part in parts] == ["sent"]


async def test_sunday_sends_daily_then_weekly_and_two_png_charts(db_engine) -> None:
    (
        session_factory,
        user,
        _,
        bot,
        _,
        scheduler,
        tracking,
    ) = await make_report_scheduler(db_engine, activated_at=at(2, 8), now=at(16, 9))
    await tracking.log_session(LogEventCommand(user.id, 10, at(15, 10), EventSource.BACKFILL))
    await tracking.log_session(LogEventCommand(user.id, 11, at(15, 11), EventSource.BACKFILL))

    await scheduler.recover()
    await scheduler.tick()

    assert len(bot.sent_texts) == 2
    assert bot.sent_texts[0].startswith("📊 Ежедневный отчёт")
    assert bot.sent_texts[1].startswith("📈 Еженедельный отчёт")
    assert [caption for caption, _ in bot.sent_photos] == [
        "Неделя по дням",
        "Весь период по неделям",
    ]
    assert all(image.startswith(b"\x89PNG\r\n\x1a\n") for _, image in bot.sent_photos)
    async with session_factory() as session:
        deliveries = (
            await session.scalars(
                select(ReportDeliveryRow).order_by(ReportDeliveryRow.period_end_utc)
            )
        ).all()
        parts = (
            await session.scalars(
                select(ReportDeliveryPartRow).order_by(
                    ReportDeliveryPartRow.report_delivery_id,
                    ReportDeliveryPartRow.ordinal,
                )
            )
        ).all()
    assert [row.report_type for row in deliveries] == ["daily", "weekly"]
    assert [row.status for row in deliveries] == ["sent", "sent"]
    assert [part.part_type for part in parts] == [
        "text",
        "text",
        "current_week_chart",
        "history_chart",
    ]


async def test_first_weekly_report_is_partial_and_does_not_fake_comparison(db_engine) -> None:
    _, _, _, bot, _, scheduler, _ = await make_report_scheduler(
        db_engine,
        activated_at=at(12, 15),
        now=at(16, 9),
    )

    await scheduler.recover()
    await scheduler.tick()

    weekly = next(text for text in bot.sent_texts if text.startswith("📈"))
    assert "неполная неделя" in weekly
    assert "Затронуто дней: 4 (первый день частичный)" in weekly
    assert "сравнение пока недоступно" in weekly


async def test_restart_retries_unknown_report_part_once_from_immutable_snapshot(
    db_engine,
) -> None:
    (
        session_factory,
        _,
        _,
        bot,
        report_store,
        scheduler,
        _,
    ) = await make_report_scheduler(db_engine, activated_at=at(14, 8), now=at(15, 9))
    await report_store.ensure_due_deliveries(at(15, 9))
    work = await report_store.claim_due(at(15, 9))
    assert work is not None
    report = await ReportBuilder(
        DatabaseGateway(session_factory), DisabledReportCommentator()
    ).build_delivery(
        work,
        generated_at=at(15, 9),
    )
    snapshot_json = await report_store.save_generated(work, report, now=at(15, 9))
    part = await report_store.claim_next_part(work.id, now=at(15, 9))
    assert part is not None

    await report_store.recover(at(15, 9) + timedelta(seconds=1))
    await scheduler.tick()

    assert len(bot.sent_texts) == 1
    async with session_factory() as session:
        delivery = await session.get(ReportDeliveryRow, work.id)
        stored_part = await session.get(ReportDeliveryPartRow, part.id)
    assert delivery is not None and delivery.status == "sent"
    assert delivery.snapshot_json == snapshot_json
    assert stored_part is not None and stored_part.status == "sent"
    assert stored_part.attempt_count == 2
