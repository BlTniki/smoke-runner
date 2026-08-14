"""Tests for wake cycles, wake-to-first states, and completed actual gaps."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import UtcPeriod
from smoke_runner.domain.metrics import (
    DuplicateWakeLocalDateError,
    WakeToFirstStatus,
    actual_gaps_ending_in,
    build_daily_metrics,
    build_wake_cycles,
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


def timeline_for(sessions: list[SmokingSession]):
    return build_timeline(
        sessions,
        [
            IntervalChange(
                id=1,
                effective_at=at(8),
                interval=TargetInterval.hours(1),
            )
        ],
    )


def test_wake_cycle_ignores_sessions_before_wake_and_stops_at_next_wake() -> None:
    wakes = [
        WakeEvent(id=1, occurred_at=at(9, 8)),
        WakeEvent(id=2, occurred_at=at(10, 7)),
    ]
    sessions = [
        SmokingSession(id=1, occurred_at=at(9, 7)),
        SmokingSession(id=2, occurred_at=at(9, 9)),
        SmokingSession(id=3, occurred_at=at(10, 6)),
        SmokingSession(id=4, occurred_at=at(10, 7)),
    ]

    first_cycle, second_cycle = build_wake_cycles(wakes, sessions, timezone=ZoneInfo("UTC"))

    assert first_cycle.first_session == sessions[1]
    assert first_cycle.last_session_before_next_wake == sessions[2]
    assert first_cycle.wake_to_first == timedelta(hours=1)
    assert second_cycle.first_session == sessions[3]
    assert second_cycle.last_session_before_next_wake is None


def test_wake_metric_belongs_to_wake_date_even_when_first_session_is_after_midnight() -> None:
    activation = at(9)
    sessions = [SmokingSession(id=1, occurred_at=at(10, 1))]
    wakes = [WakeEvent(id=1, occurred_at=at(9, 23))]

    metrics = build_daily_metrics(
        timeline_for(sessions),
        wakes,
        local_date=date(2026, 8, 9),
        activated_at=activation,
        timezone=ZoneInfo("UTC"),
    )

    assert metrics.session_count == 0
    assert metrics.wake_to_first.status is WakeToFirstStatus.MEASURED
    assert metrics.wake_to_first.duration == timedelta(hours=2)


def test_missing_wake_no_session_and_zero_duration_are_distinct() -> None:
    activation = at(9)
    timezone = ZoneInfo("UTC")

    missing = build_daily_metrics(
        timeline_for([]),
        [],
        local_date=date(2026, 8, 9),
        activated_at=activation,
        timezone=timezone,
    )
    no_session = build_daily_metrics(
        timeline_for([]),
        [WakeEvent(id=1, occurred_at=at(9, 8))],
        local_date=date(2026, 8, 9),
        activated_at=activation,
        timezone=timezone,
    )
    exact_session = SmokingSession(id=1, occurred_at=at(9, 8))
    zero = build_daily_metrics(
        timeline_for([exact_session]),
        [WakeEvent(id=1, occurred_at=at(9, 8))],
        local_date=date(2026, 8, 9),
        activated_at=activation,
        timezone=timezone,
    )

    assert missing.wake_to_first.status is WakeToFirstStatus.MISSING_WAKE
    assert no_session.wake_to_first.status is WakeToFirstStatus.NO_SESSION_AFTER_WAKE
    assert zero.wake_to_first.status is WakeToFirstStatus.MEASURED
    assert zero.wake_to_first.duration == timedelta(0)


def test_duplicate_primary_wakes_on_one_local_date_are_rejected() -> None:
    with pytest.raises(DuplicateWakeLocalDateError):
        build_wake_cycles(
            [
                WakeEvent(id=1, occurred_at=at(9, 7)),
                WakeEvent(id=2, occurred_at=at(9, 8)),
            ],
            [],
            timezone=ZoneInfo("UTC"),
        )


def test_gap_is_assigned_by_ending_session_even_when_it_crosses_period_start() -> None:
    sessions = [
        SmokingSession(id=1, occurred_at=at(8, 23)),
        SmokingSession(id=2, occurred_at=at(9, 1)),
        SmokingSession(id=3, occurred_at=at(9, 3)),
    ]
    period = UtcPeriod(start=at(9), end=at(10))

    gaps = actual_gaps_ending_in(timeline_for(sessions), period)

    assert [gap.duration for gap in gaps] == [timedelta(hours=2), timedelta(hours=2)]
    assert gaps[0].started_by == sessions[0]
