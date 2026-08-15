"""Maintain one durable editable bot message per authorized user."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from smoke_runner.application.security import AuthenticatedUser
from smoke_runner.domain.clock import Clock
from smoke_runner.infrastructure.db.gateway import DatabaseGateway


class ScreenManager:
    def __init__(self, gateway: DatabaseGateway, clock: Clock) -> None:
        self._gateway = gateway
        self._clock = clock

    async def show(
        self,
        *,
        bot: Bot,
        user: AuthenticatedUser,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup,
        screen_kind: str,
    ) -> int:
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
            message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            message_id = message.message_id
        await self._gateway.save_dashboard_state(
            user_id=user.id,
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
            screen_kind=screen_kind,
            now=self._clock.now(),
        )
        return message_id
