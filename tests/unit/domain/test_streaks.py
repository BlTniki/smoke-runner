"""Tests for violation-free completed-day streaks."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import IntervalChange, SmokingSession, TargetInterval
from smoke_runner.domain.streaks import (
    TodayStreakStatus,
    calculate_completed_streaks,
    calculate_dashboard_streaks,
)
from smoke_runner.domain.timeline import build_timeline


def at(day: int, hour: int = 0, minute: int = 0) -> UtcInstant:
    return UtcInstant(datetime(2026, 8, day, hour, minute, tzinfo=UTC))


def build_history(*, backfill_violation_on_day_12: bool = False):
    sessions = [
        SmokingSession(id=1, occurred_at=at(9, 8)),
        SmokingSession(id=2, occurred_at=at(10, 8)),
        SmokingSession(id=3, occurred_at=at(10, 8, 30)),
    ]
    if backfill_violation_on_day_12:
        sessions.extend(
            [
                SmokingSession(id=4, occurred_at=at(12, 8)),
                SmokingSession(id=5, occurred_at=at(12, 8, 30)),
            ]
        )
    return build_timeline(
        sessions,
        [
            IntervalChange(
                id=1,
                effective_at=at(9),
                interval=TargetInterval.hours(1),
            )
        ],
    )


def test_zero_session_days_continue_the_streak() -> None:
    streaks = calculate_completed_streaks(
        build_history(),
        activated_at=at(9),
        through_local_date=date(2026, 8, 12),
        timezone=ZoneInfo("UTC"),
    )

    assert streaks.current == 2
    assert streaks.record == 2


def test_backfilled_violation_recomputes_current_and_record_streak() -> None:
    before = calculate_completed_streaks(
        build_history(),
        activated_at=at(9),
        through_local_date=date(2026, 8, 12),
        timezone=ZoneInfo("UTC"),
    )
    after = calculate_completed_streaks(
        build_history(backfill_violation_on_day_12=True),
        activated_at=at(9),
        through_local_date=date(2026, 8, 12),
        timezone=ZoneInfo("UTC"),
    )

    assert before.current == 2
    assert after.current == 0
    assert after.record == 1


def test_dashboard_does_not_add_unfinished_today_to_confirmed_streak() -> None:
    result = calculate_dashboard_streaks(
        build_history(),
        activated_at=at(9),
        now=at(13, 12),
        timezone=ZoneInfo("UTC"),
    )

    assert result.completed.current == 2
    assert result.today is TodayStreakStatus.NO_VIOLATION_YET


def test_dashboard_marks_today_violation_without_changing_completed_length() -> None:
    history = build_timeline(
        [
            *[assessment.session for assessment in build_history().sessions],
            SmokingSession(id=10, occurred_at=at(13, 8)),
            SmokingSession(id=11, occurred_at=at(13, 8, 30)),
        ],
        [
            IntervalChange(
                id=1,
                effective_at=at(9),
                interval=TargetInterval.hours(1),
            )
        ],
    )

    result = calculate_dashboard_streaks(
        history,
        activated_at=at(9),
        now=at(13, 12),
        timezone=ZoneInfo("UTC"),
    )

    assert result.completed.current == 2
    assert result.today is TodayStreakStatus.VIOLATION
