"""Tests for calendar boundaries and explicit DST handling."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import (
    AmbiguousLocalTimeError,
    LocalTimeOccurrence,
    NonexistentLocalTimeError,
    daily_report_schedule,
    first_weekly_delivery_date,
    local_day_period,
    resolve_local_datetime,
    weekly_report_schedule,
)


def test_regular_local_time_resolves_to_utc() -> None:
    result = resolve_local_datetime(
        datetime(2026, 8, 14, 12),
        ZoneInfo("Europe/Moscow"),
    )

    assert result == UtcInstant(datetime(2026, 8, 14, 9, tzinfo=UTC))


def test_ambiguous_local_time_requires_explicit_first_or_second_choice() -> None:
    timezone = ZoneInfo("America/New_York")
    local_value = datetime(2026, 11, 1, 1, 30)

    with pytest.raises(AmbiguousLocalTimeError) as error_info:
        resolve_local_datetime(local_value, timezone)

    error = error_info.value
    assert error.first == UtcInstant(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    assert error.second == UtcInstant(datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert resolve_local_datetime(
        local_value, timezone, occurrence=LocalTimeOccurrence.FIRST
    ) == UtcInstant(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    assert resolve_local_datetime(
        local_value, timezone, occurrence=LocalTimeOccurrence.SECOND
    ) == UtcInstant(datetime(2026, 11, 1, 6, 30, tzinfo=UTC))


def test_nonexistent_local_time_is_rejected() -> None:
    with pytest.raises(NonexistentLocalTimeError):
        resolve_local_datetime(
            datetime(2026, 3, 8, 2, 30),
            ZoneInfo("America/New_York"),
        )


def test_local_conversion_rejects_aware_input() -> None:
    with pytest.raises(ValueError):
        resolve_local_datetime(datetime(2026, 8, 14, tzinfo=UTC), ZoneInfo("UTC"))


def test_local_day_uses_calendar_boundaries_across_dst() -> None:
    period = local_day_period(date(2026, 3, 8), ZoneInfo("America/New_York"))

    assert period.end - period.start == timedelta(hours=23)


def test_daily_report_at_nine_covers_previous_local_day() -> None:
    schedule = daily_report_schedule(date(2026, 8, 14), ZoneInfo("Europe/Moscow"))

    assert schedule.delivery_at == UtcInstant(datetime(2026, 8, 14, 6, tzinfo=UTC))
    assert schedule.reported_local_date == date(2026, 8, 13)
    assert schedule.period.start == UtcInstant(datetime(2026, 8, 12, 21, tzinfo=UTC))
    assert schedule.period.end == UtcInstant(datetime(2026, 8, 13, 21, tzinfo=UTC))


def test_weekly_report_covers_completed_sunday_through_saturday() -> None:
    timezone = ZoneInfo("Europe/Moscow")
    activated_at = UtcInstant(datetime(2026, 8, 1, tzinfo=UTC))

    schedule = weekly_report_schedule(
        date(2026, 8, 16),
        timezone,
        activated_at=activated_at,
    )

    assert schedule.delivery_at == UtcInstant(datetime(2026, 8, 16, 6, tzinfo=UTC))
    assert schedule.full_period.start == UtcInstant(datetime(2026, 8, 8, 21, tzinfo=UTC))
    assert schedule.full_period.end == UtcInstant(datetime(2026, 8, 15, 21, tzinfo=UTC))
    assert schedule.start_local_date == date(2026, 8, 9)
    assert schedule.end_local_date_exclusive == date(2026, 8, 16)
    assert schedule.day_count == 7
    assert not schedule.is_partial


def test_first_week_is_clipped_to_activation_and_labeled_partial() -> None:
    timezone = ZoneInfo("Europe/Moscow")
    activated_at = resolve_local_datetime(datetime(2026, 8, 12, 15), timezone)

    schedule = weekly_report_schedule(
        date(2026, 8, 16),
        timezone,
        activated_at=activated_at,
    )

    assert schedule.period.start == activated_at
    assert schedule.start_local_date == date(2026, 8, 12)
    assert schedule.day_count == 4
    assert schedule.is_partial
    assert schedule.first_day_is_partial


@pytest.mark.parametrize(
    ("activated_local", "expected_delivery"),
    [
        (datetime(2026, 8, 10, 10), date(2026, 8, 16)),
        (datetime(2026, 8, 9, 0), date(2026, 8, 16)),
        (datetime(2026, 8, 9, 15), date(2026, 8, 16)),
    ],
)
def test_first_weekly_report_is_next_sunday_even_after_sunday_activation(
    activated_local: datetime,
    expected_delivery: date,
) -> None:
    timezone = ZoneInfo("Europe/Moscow")
    activated_at = resolve_local_datetime(activated_local, timezone)

    assert first_weekly_delivery_date(activated_at, timezone) == expected_delivery


def test_weekly_report_rejects_non_sunday_delivery_date() -> None:
    with pytest.raises(ValueError):
        weekly_report_schedule(
            date(2026, 8, 15),
            ZoneInfo("UTC"),
            activated_at=UtcInstant(datetime(2026, 8, 1, tzinfo=UTC)),
        )
