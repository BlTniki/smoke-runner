"""Tests for immutable, privacy-safe report snapshots."""

import json
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import UtcPeriod, weekly_report_schedule
from smoke_runner.domain.metrics import (
    DailyMetrics,
    WakeToFirstMetric,
    WakeToFirstStatus,
    build_weekly_metrics,
)
from smoke_runner.domain.models import IntervalChange, SmokingSession, TargetInterval
from smoke_runner.domain.report_models import (
    ComparisonOutcome,
    ComparisonUnavailableReason,
    HighlightKind,
    ReportType,
    build_daily_report_snapshot,
    build_weekly_report_snapshot,
    commentary_input_from_snapshot,
)
from smoke_runner.domain.timeline import build_timeline


def at(day: int, hour: int = 0, minute: int = 0) -> UtcInstant:
    return UtcInstant(datetime(2026, 8, day, hour, minute, tzinfo=UTC))


def daily_metrics(
    *,
    day: int,
    sessions: int,
    violations: int,
    average_earliness: timedelta | None,
    wake_duration: timedelta | None,
    streak: int,
) -> DailyMetrics:
    wake_status = (
        WakeToFirstStatus.MEASURED if wake_duration is not None else WakeToFirstStatus.MISSING_WAKE
    )
    return DailyMetrics(
        local_date=date(2026, 8, day),
        period=UtcPeriod(start=at(day), end=at(day + 1)),
        is_partial=False,
        session_count=sessions,
        classifiable_session_count=max(0, sessions - 1),
        violation_count=violations,
        average_earliness=average_earliness,
        maximum_earliness=average_earliness,
        wake_to_first=WakeToFirstMetric(
            status=wake_status,
            wake=None,
            first_session=None,
            duration=wake_duration,
        ),
        streak_at_end=streak,
    )


def test_daily_snapshot_contains_deltas_directions_and_ranked_highlight() -> None:
    previous = daily_metrics(
        day=13,
        sessions=5,
        violations=2,
        average_earliness=timedelta(minutes=20),
        wake_duration=timedelta(hours=1),
        streak=2,
    )
    current = daily_metrics(
        day=14,
        sessions=3,
        violations=1,
        average_earliness=timedelta(minutes=10),
        wake_duration=timedelta(hours=2),
        streak=3,
    )

    snapshot = build_daily_report_snapshot(
        current,
        previous=previous,
        generated_at=at(15, 9),
        timezone_name="UTC",
    )

    assert snapshot.report_type is ReportType.DAILY
    assert snapshot.comparisons.session_count.delta == -2
    assert snapshot.comparisons.session_count.outcome is ComparisonOutcome.IMPROVED
    assert snapshot.comparisons.wake_to_first.delta == 60 * 60 * 1_000_000
    assert snapshot.comparisons.wake_to_first.outcome is ComparisonOutcome.IMPROVED
    assert snapshot.highlight.kind is HighlightKind.FEWER_VIOLATIONS
    assert snapshot.highlight.delta == -1


def test_missing_previous_period_and_missing_values_are_not_zero() -> None:
    current = daily_metrics(
        day=14,
        sessions=0,
        violations=0,
        average_earliness=None,
        wake_duration=None,
        streak=1,
    )
    without_previous = build_daily_report_snapshot(
        current,
        previous=None,
        generated_at=at(15, 9),
        timezone_name="UTC",
    )
    with_missing_previous_value = build_daily_report_snapshot(
        current,
        previous=daily_metrics(
            day=13,
            sessions=0,
            violations=0,
            average_earliness=None,
            wake_duration=None,
            streak=0,
        ),
        generated_at=at(15, 9),
        timezone_name="UTC",
    )

    assert (
        without_previous.comparisons.session_count.unavailable_reason
        is ComparisonUnavailableReason.PREVIOUS_PERIOD_UNAVAILABLE
    )
    assert (
        with_missing_previous_value.comparisons.average_earliness.unavailable_reason
        is ComparisonUnavailableReason.BOTH_VALUES_MISSING
    )
    assert with_missing_previous_value.current.average_earliness is None


def test_snapshot_is_frozen_and_json_serializable_without_custom_encoder() -> None:
    snapshot = build_daily_report_snapshot(
        daily_metrics(
            day=14,
            sessions=0,
            violations=0,
            average_earliness=None,
            wake_duration=None,
            streak=1,
        ),
        previous=None,
        generated_at=at(15, 9),
        timezone_name="UTC",
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.timezone_name = "Europe/Moscow"  # type: ignore[misc]
    serialized = json.dumps(asdict(snapshot), ensure_ascii=False)
    assert '"report_type": "daily"' in serialized


def test_weekly_snapshot_sorts_history_and_blocks_partial_total_comparison() -> None:
    timezone = ZoneInfo("UTC")
    activation = at(12, 15)
    timeline = build_timeline(
        [
            SmokingSession(id=1, occurred_at=at(12, 16)),
            SmokingSession(id=2, occurred_at=at(17, 16)),
        ],
        [
            IntervalChange(
                id=1,
                effective_at=activation,
                interval=TargetInterval.hours(1),
            )
        ],
    )
    partial = build_weekly_metrics(
        timeline,
        [],
        schedule=weekly_report_schedule(date(2026, 8, 16), timezone, activated_at=activation),
        activated_at=activation,
        timezone=timezone,
    )
    full = build_weekly_metrics(
        timeline,
        [],
        schedule=weekly_report_schedule(date(2026, 8, 23), timezone, activated_at=activation),
        activated_at=activation,
        timezone=timezone,
    )

    snapshot = build_weekly_report_snapshot(
        full,
        previous=partial,
        history=[full, partial],
        generated_at=at(23, 9),
        timezone_name="UTC",
    )

    assert (
        snapshot.comparisons.total_sessions.unavailable_reason
        is ComparisonUnavailableReason.PARTIAL_PERIOD
    )
    assert [point.start_local_date for point in snapshot.history_chart] == [
        "2026-08-12",
        "2026-08-16",
    ]
    assert len(snapshot.current_week_chart) == 7


def test_commentary_input_contains_only_report_facts_and_no_identifiers() -> None:
    snapshot = build_daily_report_snapshot(
        daily_metrics(
            day=14,
            sessions=1,
            violations=0,
            average_earliness=None,
            wake_duration=timedelta(hours=2),
            streak=3,
        ),
        previous=None,
        generated_at=at(15, 9),
        timezone_name="Europe/Moscow",
    )

    commentary_input = commentary_input_from_snapshot(snapshot)
    payload = json.dumps(asdict(commentary_input), ensure_ascii=False).lower()

    assert commentary_input.metrics == snapshot.current
    assert "telegram" not in payload
    assert "invite" not in payload
    assert "username" not in payload
    assert "chat_id" not in payload
    assert "user_id" not in payload
