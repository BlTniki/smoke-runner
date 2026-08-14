"""Violation-free day streak calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import local_date_of
from smoke_runner.domain.timeline import Timeline


@dataclass(frozen=True, slots=True)
class CompletedStreaks:
    """Current and record streak through a completed local day."""

    current: int
    record: int


class TodayStreakStatus(StrEnum):
    """Separate status for the unfinished local day."""

    NO_VIOLATION_YET = "no_violation_yet"
    VIOLATION = "violation"


@dataclass(frozen=True, slots=True)
class DashboardStreaks:
    """Confirmed streaks plus today's unconfirmed state."""

    completed: CompletedStreaks
    today: TodayStreakStatus


def calculate_completed_streaks(
    timeline: Timeline,
    *,
    activated_at: UtcInstant,
    through_local_date: date,
    timezone: ZoneInfo,
) -> CompletedStreaks:
    """Recalculate streaks from activation through an inclusive completed day."""
    activation_date = local_date_of(activated_at, timezone)
    if through_local_date < activation_date:
        return CompletedStreaks(current=0, record=0)

    violation_dates = {
        local_date_of(assessment.session.occurred_at, timezone)
        for assessment in timeline.sessions
        if assessment.is_violation and assessment.session.occurred_at >= activated_at
    }

    current = 0
    record = 0
    cursor = activation_date
    while cursor <= through_local_date:
        if cursor in violation_dates:
            current = 0
        else:
            current += 1
            record = max(record, current)
        cursor += timedelta(days=1)

    return CompletedStreaks(current=current, record=record)


def calculate_dashboard_streaks(
    timeline: Timeline,
    *,
    activated_at: UtcInstant,
    now: UtcInstant,
    timezone: ZoneInfo,
) -> DashboardStreaks:
    """Keep the current day out of the confirmed length while showing its status."""
    today = local_date_of(now, timezone)
    completed = calculate_completed_streaks(
        timeline,
        activated_at=activated_at,
        through_local_date=today - timedelta(days=1),
        timezone=timezone,
    )
    has_violation_today = any(
        assessment.is_violation
        and activated_at <= assessment.session.occurred_at <= now
        and local_date_of(assessment.session.occurred_at, timezone) == today
        for assessment in timeline.sessions
    )
    return DashboardStreaks(
        completed=completed,
        today=(
            TodayStreakStatus.VIOLATION
            if has_violation_today
            else TodayStreakStatus.NO_VIOLATION_YET
        ),
    )
