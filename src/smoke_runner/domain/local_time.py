"""Explicit conversion between user-local calendar values and UTC."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from smoke_runner.domain.clock import UtcInstant


class LocalTimeError(ValueError):
    """Base error for invalid conversion of a user-local time."""


class NonexistentLocalTimeError(LocalTimeError):
    """Raised when a wall-clock value is skipped by a timezone transition."""


class AmbiguousLocalTimeError(LocalTimeError):
    """Raised when a wall-clock value maps to two absolute instants."""

    def __init__(
        self,
        local_value: datetime,
        timezone_name: str,
        first: UtcInstant,
        second: UtcInstant,
    ) -> None:
        super().__init__(f"Local time {local_value} is ambiguous in {timezone_name}")
        self.local_value = local_value
        self.timezone_name = timezone_name
        self.first = first
        self.second = second


class LocalTimeOccurrence(StrEnum):
    """User choice for an ambiguous local time."""

    FIRST = "first"
    SECOND = "second"


@dataclass(frozen=True, slots=True)
class UtcPeriod:
    """Half-open interval of absolute time."""

    start: UtcInstant
    end: UtcInstant

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("Period start must be before period end")

    def contains(self, instant: UtcInstant) -> bool:
        return self.start <= instant < self.end


@dataclass(frozen=True, slots=True)
class DailyReportSchedule:
    """Delivery instant and covered local day for a daily report."""

    delivery_at: UtcInstant
    reported_local_date: date
    period: UtcPeriod


@dataclass(frozen=True, slots=True)
class WeeklyReportSchedule:
    """Delivery instant and effective coverage of a completed week."""

    delivery_at: UtcInstant
    period: UtcPeriod
    full_period: UtcPeriod
    start_local_date: date
    end_local_date_exclusive: date
    day_count: int
    is_partial: bool
    first_day_is_partial: bool


def resolve_local_datetime(
    local_value: datetime,
    timezone: ZoneInfo,
    *,
    occurrence: LocalTimeOccurrence | None = None,
) -> UtcInstant:
    """Resolve a naive wall-clock value, rejecting DST gaps and unresolved folds."""
    if local_value.tzinfo is not None:
        raise ValueError("Local datetime must be naive")
    if local_value.microsecond != 0:
        raise ValueError("Local datetime must have whole-second precision")

    candidates: dict[int, UtcInstant] = {}
    for fold in (0, 1):
        aware = local_value.replace(tzinfo=timezone, fold=fold)
        utc_value = aware.astimezone(UTC)
        round_trip = utc_value.astimezone(timezone).replace(tzinfo=None)
        if round_trip == local_value:
            instant = UtcInstant(utc_value)
            candidates[instant.to_unix_seconds()] = instant

    ordered_candidates = tuple(candidates[key] for key in sorted(candidates))
    if not ordered_candidates:
        raise NonexistentLocalTimeError(
            f"Local time {local_value} does not exist in {timezone.key}"
        )
    if len(ordered_candidates) == 1:
        return ordered_candidates[0]

    first, second = ordered_candidates
    if occurrence is LocalTimeOccurrence.FIRST:
        return first
    if occurrence is LocalTimeOccurrence.SECOND:
        return second
    raise AmbiguousLocalTimeError(local_value, timezone.key, first, second)


def local_date_of(instant: UtcInstant, timezone: ZoneInfo) -> date:
    """Return the user's calendar date containing an absolute instant."""
    return instant.value.astimezone(timezone).date()


def start_of_local_day(local_date: date, timezone: ZoneInfo) -> UtcInstant:
    """Resolve local midnight through the same explicit transition rules."""
    return resolve_local_datetime(datetime.combine(local_date, time.min), timezone)


def local_day_period(local_date: date, timezone: ZoneInfo) -> UtcPeriod:
    """Return exact UTC bounds for one local calendar day."""
    return UtcPeriod(
        start=start_of_local_day(local_date, timezone),
        end=start_of_local_day(local_date + timedelta(days=1), timezone),
    )


def daily_report_schedule(delivery_local_date: date, timezone: ZoneInfo) -> DailyReportSchedule:
    """Schedule 09:00 delivery for the preceding local day."""
    delivery_at = resolve_local_datetime(
        datetime.combine(delivery_local_date, time(hour=9)),
        timezone,
    )
    reported_date = delivery_local_date - timedelta(days=1)
    return DailyReportSchedule(
        delivery_at=delivery_at,
        reported_local_date=reported_date,
        period=local_day_period(reported_date, timezone),
    )


def first_weekly_delivery_date(activated_at: UtcInstant, timezone: ZoneInfo) -> date:
    """Return the first Sunday strictly after activation's local boundary."""
    activated_date = local_date_of(activated_at, timezone)
    days_until_sunday = (6 - activated_date.weekday()) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7
    return activated_date + timedelta(days=days_until_sunday)


def weekly_report_schedule(
    delivery_sunday: date,
    timezone: ZoneInfo,
    *,
    activated_at: UtcInstant,
) -> WeeklyReportSchedule:
    """Schedule Sunday 09:00 for the completed Sunday-through-Saturday period."""
    if delivery_sunday.weekday() != 6:
        raise ValueError("Weekly report delivery date must be Sunday")

    start_date = delivery_sunday - timedelta(days=7)
    full_period = UtcPeriod(
        start=start_of_local_day(start_date, timezone),
        end=start_of_local_day(delivery_sunday, timezone),
    )
    if activated_at >= full_period.end:
        raise ValueError("Activation must occur before the reported period ends")

    effective_start = max(full_period.start, activated_at)
    effective_start_date = local_date_of(effective_start, timezone)
    effective_period = UtcPeriod(start=effective_start, end=full_period.end)
    is_partial = effective_start > full_period.start
    first_day_start = start_of_local_day(effective_start_date, timezone)

    return WeeklyReportSchedule(
        delivery_at=resolve_local_datetime(
            datetime.combine(delivery_sunday, time(hour=9)),
            timezone,
        ),
        period=effective_period,
        full_period=full_period,
        start_local_date=effective_start_date,
        end_local_date_exclusive=delivery_sunday,
        day_count=(delivery_sunday - effective_start_date).days,
        is_partial=is_partial,
        first_day_is_partial=effective_start > first_day_start,
    )
