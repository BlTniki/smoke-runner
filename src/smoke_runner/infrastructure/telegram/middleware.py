# ruff: noqa: RUF001
"""Private-chat and database-backed authorization middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from smoke_runner.infrastructure.db.gateway import DatabaseGateway


class PrivateAuthMiddleware(BaseMiddleware):
    """Reject non-private chats and attach an active user, if one exists."""

    def __init__(self, gateway: DatabaseGateway) -> None:
        self._gateway = gateway

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.chat.type != "private":
                await event.answer("Я работаю только в личном чате.")
                return None
            telegram_user = event.from_user
        elif isinstance(event, CallbackQuery):
            if event.message is None or event.message.chat.type != "private":
                await event.answer("Открой личный чат с ботом.", show_alert=True)
                return None
            telegram_user = event.from_user
        else:
            return await handler(event, data)

        data["auth_user"] = (
            None
            if telegram_user is None
            else await self._gateway.find_active_user(telegram_user.id)
        )
        return await handler(event, data)
