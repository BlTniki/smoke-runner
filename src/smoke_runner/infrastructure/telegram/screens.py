"""Maintain one durable editable bot message per authorized user."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from smoke_runner.application.security import AuthenticatedUser
from smoke_runner.domain.clock import Clock, UtcInstant
from smoke_runner.infrastructure.db.gateway import DatabaseGateway

DASHBOARD_ACTIVE_WINDOW = timedelta(minutes=30)
DASHBOARD_REFRESH_INTERVAL = timedelta(minutes=5)


class ScreenLocks:
    """Serialize foreground and scheduler edits of each user's one message."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def for_user(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())


class ScreenManager:
    def __init__(
        self,
        gateway: DatabaseGateway,
        clock: Clock,
        *,
        locks: ScreenLocks | None = None,
        scheduler_wakeup: asyncio.Event | None = None,
    ) -> None:
        self._gateway = gateway
        self._clock = clock
        self._locks = locks or ScreenLocks()
        self._scheduler_wakeup = scheduler_wakeup

    async def show(
        self,
        *,
        bot: Bot,
        user: AuthenticatedUser,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup,
        screen_kind: str,
        target_at: UtcInstant | None = None,
    ) -> int:
        async with self._locks.for_user(user.id):
            state = await self._gateway.get_dashboard_state(user.id)
            message_id = state.telegram_message_id if state is not None else None
            if message_id is not None and state is not None and state.telegram_chat_id == chat_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                except TelegramBadRequest as error:
                    if "message is not modified" not in error.message.lower():
                        message_id = None
            if message_id is None:
                message = await bot.send_message(
                    chat_id=chat_id, text=text, reply_markup=reply_markup
                )
                message_id = message.message_id

            now = self._clock.now()
            active_until: UtcInstant | None = None
            next_refresh: UtcInstant | None = None
            if screen_kind == "dashboard":
                active_until = now + DASHBOARD_ACTIVE_WINDOW
                next_refresh = next_dashboard_refresh(now, active_until, target_at)
            await self._gateway.save_dashboard_state(
                user_id=user.id,
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
                screen_kind=screen_kind,
                now=now,
                active_until=active_until,
                next_refresh_at=next_refresh,
            )
        if self._scheduler_wakeup is not None:
            self._scheduler_wakeup.set()
        return message_id


def next_dashboard_refresh(
    now: UtcInstant,
    active_until: UtcInstant,
    target_at: UtcInstant | None,
) -> UtcInstant:
    candidates = [now + DASHBOARD_REFRESH_INTERVAL, active_until]
    if target_at is not None and target_at > now:
        candidates.append(target_at)
    return min(candidates)
