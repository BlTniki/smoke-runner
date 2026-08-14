"""Tests for absolute UTC time values and the production clock."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from smoke_runner.domain.clock import SystemClock, UtcInstant


def test_utc_instant_normalizes_an_aware_datetime() -> None:
    source = datetime(2026, 8, 14, 15, 0, tzinfo=timezone(timedelta(hours=3)))

    instant = UtcInstant(source)

    assert instant.value == datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 8, 14, 12, 0),
        datetime(2026, 8, 14, 12, 0, 0, 1, tzinfo=UTC),
    ],
)
def test_utc_instant_rejects_ambiguous_persistence_values(value: datetime) -> None:
    with pytest.raises(ValueError):
        UtcInstant(value)


def test_unix_seconds_round_trip() -> None:
    assert UtcInstant.from_unix_seconds(1_786_709_400).to_unix_seconds() == 1_786_709_400


def test_unix_seconds_rejects_boolean() -> None:
    with pytest.raises(TypeError):
        UtcInstant.from_unix_seconds(True)


def test_system_clock_uses_whole_second_utc_precision() -> None:
    current = SystemClock().now()

    assert current.value.tzinfo is UTC
    assert current.value.microsecond == 0
