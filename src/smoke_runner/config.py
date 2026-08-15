"""Runtime configuration loaded from environment variables or ``.env``."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process settings; secrets are never included in ``repr``."""

    model_config = SettingsConfigDict(
        env_prefix="SMOKE_RUNNER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr = Field(min_length=20)
    invite_pepper: SecretStr = Field(min_length=32)
    admin_telegram_user_id: int | None = Field(default=None, gt=0)
    database_path: Path = Path("data/smoke-runner.sqlite3")
    default_timezone: str = "Europe/Moscow"
    invite_ttl_hours: int = Field(default=168, ge=1, le=24 * 30)
    admin_bootstrap_ttl_minutes: int = Field(default=30, ge=5, le=24 * 60)
    polling_concurrency_limit: int = Field(default=20, ge=1, le=100)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"Unknown IANA timezone: {value}") from error
        return value
