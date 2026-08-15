"""Application composition root and polling lifecycle."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timedelta
from typing import cast

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from smoke_runner.application.ports import TrackingUnitOfWork
from smoke_runner.application.security import (
    AdminBootstrapService,
    InviteService,
    IssuedAdminBootstrapCode,
)
from smoke_runner.application.tracking import TrackingService
from smoke_runner.config import Settings
from smoke_runner.domain.clock import SystemClock
from smoke_runner.infrastructure.ai_commentary import DisabledReportCommentator
from smoke_runner.infrastructure.db.engine import create_database_engine, create_session_factory
from smoke_runner.infrastructure.db.gateway import DatabaseGateway
from smoke_runner.infrastructure.db.migrations import upgrade_database
from smoke_runner.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from smoke_runner.infrastructure.reports import ReportBuilder, ReportDeliveryStore
from smoke_runner.infrastructure.scheduler import DurableScheduler, SchedulerStore
from smoke_runner.infrastructure.telegram.middleware import PrivateAuthMiddleware
from smoke_runner.infrastructure.telegram.routers import BotServices, build_router
from smoke_runner.infrastructure.telegram.screens import ScreenLocks, ScreenManager


def run(settings: Settings) -> None:
    """Migrate the database and run one polling process until stopped."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    upgrade_database(settings.database_path)
    asyncio.run(run_polling(settings))


async def run_polling(settings: Settings) -> None:
    engine = create_database_engine(settings.database_path)
    session_factory = create_session_factory(engine)
    clock = SystemClock()
    gateway = DatabaseGateway(session_factory)
    admin_bootstrap_service = AdminBootstrapService(
        gateway,
        pepper=settings.invite_pepper.get_secret_value(),
        clock=clock,
        ttl=timedelta(minutes=settings.admin_bootstrap_ttl_minutes),
        timezone_name=settings.default_timezone,
    )
    if settings.admin_telegram_user_id is not None:
        await gateway.bootstrap_admin(
            telegram_user_id=settings.admin_telegram_user_id,
            timezone_name=settings.default_timezone,
            now=clock.now(),
        )
    else:
        issued_code = await admin_bootstrap_service.issue()
        if issued_code is not None:
            print(format_admin_bootstrap_notice(issued_code), file=sys.stderr, flush=True)

    def uow_factory() -> TrackingUnitOfWork:
        return cast(TrackingUnitOfWork, SqlAlchemyUnitOfWork(session_factory))

    tracking = TrackingService(uow_factory, clock)
    invite_service = InviteService(
        gateway,
        pepper=settings.invite_pepper.get_secret_value(),
        clock=clock,
        ttl=timedelta(hours=settings.invite_ttl_hours),
        timezone_name=settings.default_timezone,
    )
    scheduler_wakeup = asyncio.Event()
    report_builder = ReportBuilder(gateway, DisabledReportCommentator())
    screen_locks = ScreenLocks()
    screens = ScreenManager(
        gateway,
        clock,
        locks=screen_locks,
        scheduler_wakeup=scheduler_wakeup,
    )
    services = BotServices(
        gateway=gateway,
        admin_bootstrap_service=admin_bootstrap_service,
        invite_service=invite_service,
        tracking=tracking,
        screens=screens,
        clock=clock,
        scheduler_wakeup=scheduler_wakeup,
        report_builder=report_builder,
    )
    router = build_router(services)
    middleware = PrivateAuthMiddleware(gateway)
    router.message.outer_middleware(middleware)
    router.callback_query.outer_middleware(middleware)

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    bot = Bot(token=settings.bot_token.get_secret_value())
    scheduler = DurableScheduler(
        store=SchedulerStore(session_factory),
        gateway=gateway,
        bot=bot,
        clock=clock,
        wakeup=scheduler_wakeup,
        screen_locks=screen_locks,
        report_store=ReportDeliveryStore(session_factory),
        report_builder=report_builder,
    )
    try:
        await scheduler.recover()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(scheduler.run(), name="durable-scheduler")
            try:
                await dispatcher.start_polling(
                    bot,
                    tasks_concurrency_limit=settings.polling_concurrency_limit,
                    close_bot_session=False,
                )
            finally:
                scheduler.stop()
    finally:
        await bot.session.close()
        await engine.dispose()


def format_admin_bootstrap_notice(issued: IssuedAdminBootstrapCode) -> str:
    expires = issued.expires_at.value.isoformat().replace("+00:00", "Z")
    return (
        "\n"
        "Администратор ещё не привязан.\n"
        "Отправь боту в личном чате одноразовый код:\n\n"
        f"{issued.plaintext}\n\n"
        f"Код действует до {expires}. После перезапуска будет создан новый код.\n"
    )
