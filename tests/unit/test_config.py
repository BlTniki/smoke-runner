"""Settings validation tests."""

import pytest
from pydantic import ValidationError

from smoke_runner.config import Settings


def test_settings_load_required_values_from_prefixed_environment(monkeypatch) -> None:
    monkeypatch.setenv("SMOKE_RUNNER_BOT_TOKEN", "123456789:abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("SMOKE_RUNNER_INVITE_PEPPER", "p" * 32)
    monkeypatch.setenv("SMOKE_RUNNER_ADMIN_TELEGRAM_USER_ID", "42")

    settings = Settings(_env_file=None)

    assert settings.admin_telegram_user_id == 42
    assert settings.bot_token.get_secret_value().startswith("123456789:")
    assert "abcdefghijklmnopqrstuvwxyz" not in repr(settings)


def test_settings_reject_unknown_timezone(monkeypatch) -> None:
    monkeypatch.setenv("SMOKE_RUNNER_BOT_TOKEN", "123456789:abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("SMOKE_RUNNER_INVITE_PEPPER", "p" * 32)
    monkeypatch.setenv("SMOKE_RUNNER_ADMIN_TELEGRAM_USER_ID", "42")
    monkeypatch.setenv("SMOKE_RUNNER_DEFAULT_TIMEZONE", "Moon/Sea_of_Tranquility")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
