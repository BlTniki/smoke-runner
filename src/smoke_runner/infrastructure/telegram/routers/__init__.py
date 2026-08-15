"""Telegram router composition."""

from smoke_runner.infrastructure.telegram.routers.bot import BotServices, build_router

__all__ = ["BotServices", "build_router"]
