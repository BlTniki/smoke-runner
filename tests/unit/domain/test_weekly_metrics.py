"""Tests for daily and weekly deterministic aggregates."""

from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import weekly_report_schedule
from smoke_runner.domain.metrics import (
    WakeToFirstStatus,
    build_daily_metrics,
    build_weekly_metrics,
    weekly_totals_are_comparable,
)
from smoke_runner.domain.models import (
    IntervalChange,
    SmokingSession,
    TargetInterval,
    WakeEvent,
)
from smoke_runner.domain.timeline import build_timeline


def at(day: int, hour: int = 0, minute: int = 0) -> UtcInstant:
    return UtcInstant(datetime(2026, 8, day, hour, minute, tzinfo=UTC))


def test_daily_metrics_count_violations_and_keep_no_violation_average_missing() -> None:
    activation = at(9)
    timeline = build_timeline(
        [
            SmokingSession(id=1, occurred_at=at(9, 8)),
            SmokingSession(id=2, occurred_at=at(9, 8, 30)),
        ],
        [
            IntervalChange(
                id=1,
                effective_at=activation,
                interval=TargetInterval.hours(1),
            )
        ],
    )

    day_with_violation = build_daily_metrics(
        timeline,
        [],
        local_date=date(2026, 8, 9),
        activated_at=activation,
        timezone=ZoneInfo("UTC"),
    )
    empty_day = build_daily_metrics(
        timeline,
        [],
        local_date=date(2026, 8, 10),
        activated_at=activation,
        timezone=ZoneInfo("UTC"),
    )

    assert day_with_violation.session_count == 2
    assert day_with_violation.classifiable_session_count == 1
    assert day_with_violation.violation_count == 1
    assert day_with_violation.average_earliness == timedelta(minutes=30)
    assert empty_day.session_count == 0
    assert empty_day.violation_count == 0
    assert empty_day.average_earliness is None
    assert empty_day.wake_to_first.status is WakeToFirstStatus.MISSING_WAKE


def test_weekly_aggregates_extremes_gaps_wakes_and_streaks() -> None:
    timezone = ZoneInfo("UTC")
    activation = at(1)
    sessions = [
        SmokingSession(id=1, occurred_at=at(8, 23)),
        SmokingSession(id=2, occurred_at=at(9, 1)),
        SmokingSession(id=3, occurred_at=at(9, 1, 30)),
        SmokingSession(id=4, occurred_at=at(10, 3)),
        SmokingSession(id=5, occurred_at=at(10, 3, 30)),
    ]
    timeline = build_timeline(
        sessions,
        [
            IntervalChange(
                id=1,
                effective_at=activation,
                interval=TargetInterval.hours(1),
            )
        ],
    )
    wakes = [
        WakeEvent(id=1, occurred_at=at(9, 7)),
        WakeEvent(id=2, occurred_at=at(10, 7)),
        WakeEvent(id=3, occurred_at=at(11, 7)),
    ]
    schedule = weekly_report_schedule(date(2026, 8, 16), timezone, activated_at=activation)

    metrics = build_weekly_metrics(
        timeline,
        wakes,
        schedule=schedule,
        activated_at=activation,
        timezone=timezone,
    )

    assert metrics.total_sessions == 4
    assert metrics.average_sessions_per_day == Fraction(4, 7)
    assert metrics.minimum_session_count == 0
    assert metrics.minimum_session_dates == (
        date(2026, 8, 11),
        date(2026, 8, 12),
        date(2026, 8, 13),
        date(2026, 8, 14),
        date(2026, 8, 15),
    )
    assert metrics.maximum_session_count == 2
    assert metrics.maximum_session_dates == (date(2026, 8, 9), date(2026, 8, 10))
    assert metrics.total_violations == 2
    assert metrics.violation_rate == Fraction(1, 2)
    assert metrics.average_earliness == timedelta(minutes=30)
    assert metrics.maximum_earliness == timedelta(minutes=30)
    assert metrics.actual_gaps[0].duration == timedelta(hours=2)
    assert metrics.longest_actual_gap is not None
    assert metrics.longest_actual_gap.duration == timedelta(hours=25, minutes=30)
    assert metrics.average_actual_gap == timedelta(hours=7, minutes=7, seconds=30)
    assert metrics.last_sessions_before_next_wake == (sessions[4],)
    assert metrics.streak_at_end == 5
    assert metrics.record_streak == 8


def test_partial_week_uses_touched_days_and_never_compares_totals_with_full_week() -> None:
    timezone = ZoneInfo("UTC")
    activation = at(12, 15)
    timeline = build_timeline(
        [],
        [
            IntervalChange(
                id=1,
                effective_at=activation,
                interval=TargetInterval.hours(1),
            )
        ],
    )
    partial_schedule = weekly_report_schedule(date(2026, 8, 16), timezone, activated_at=activation)
    full_schedule = weekly_report_schedule(date(2026, 8, 23), timezone, activated_at=activation)

    partial = build_weekly_metrics(
        timeline,
        [],
        schedule=partial_schedule,
        activated_at=activation,
        timezone=timezone,
    )
    full = build_weekly_metrics(
        timeline,
        [],
        schedule=full_schedule,
        activated_at=activation,
        timezone=timezone,
    )

    assert partial.schedule.is_partial
    assert partial.schedule.day_count == 4
    assert partial.average_sessions_per_day == 0
    assert partial.streak_at_end == 4
    assert not full.schedule.is_partial
    assert not weekly_totals_are_comparable(partial, full)
    assert weekly_totals_are_comparable(full, full)
