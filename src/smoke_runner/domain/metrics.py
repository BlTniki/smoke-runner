"""Deterministic daily, weekly, gap, and wake-cycle metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from fractions import Fraction
from zoneinfo import ZoneInfo

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import (
    UtcPeriod,
    WeeklyReportSchedule,
    local_date_of,
    local_day_period,
)
from smoke_runner.domain.models import SmokingSession, WakeEvent
from smoke_runner.domain.streaks import calculate_completed_streaks
from smoke_runner.domain.timeline import Timeline


class DuplicateWakeLocalDateError(ValueError):
    """Raised when metrics receive two primary wakes for one local date."""


class WakeToFirstStatus(StrEnum):
    """Three distinct states required by report rendering."""

    MISSING_WAKE = "missing_wake"
    NO_SESSION_AFTER_WAKE = "no_session_after_wake"
    MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class WakeCycle:
    """Sessions bounded by one primary wake and the next primary wake."""

    wake: WakeEvent
    local_date: date
    next_wake: WakeEvent | None
    first_session: SmokingSession | None
    last_session_before_next_wake: SmokingSession | None

    @property
    def wake_to_first(self) -> timedelta | None:
        if self.first_session is None:
            return None
        return self.first_session.occurred_at - self.wake.occurred_at


@dataclass(frozen=True, slots=True)
class WakeToFirstMetric:
    """Typed wake metric that never conflates missing data and zero."""

    status: WakeToFirstStatus
    wake: WakeEvent | None
    first_session: SmokingSession | None
    duration: timedelta | None


@dataclass(frozen=True, slots=True)
class ActualGap:
    """A completed interval between adjacent smoking sessions."""

    started_by: SmokingSession
    ended_by: SmokingSession
    duration: timedelta


@dataclass(frozen=True, slots=True)
class DailyMetrics:
    """Metrics for the tracked part of one local calendar day."""

    local_date: date
    period: UtcPeriod
    is_partial: bool
    session_count: int
    classifiable_session_count: int
    violation_count: int
    average_earliness: timedelta | None
    maximum_earliness: timedelta | None
    wake_to_first: WakeToFirstMetric
    streak_at_end: int


@dataclass(frozen=True, slots=True)
class WeeklyMetrics:
    """Aggregates for a completed full or first partial week."""

    schedule: WeeklyReportSchedule
    days: tuple[DailyMetrics, ...]
    total_sessions: int
    average_sessions_per_day: Fraction
    minimum_session_count: int
    minimum_session_dates: tuple[date, ...]
    maximum_session_count: int
    maximum_session_dates: tuple[date, ...]
    total_violations: int
    classifiable_session_count: int
    violation_rate: Fraction | None
    minimum_violation_count: int
    minimum_violation_dates: tuple[date, ...]
    maximum_violation_count: int
    maximum_violation_dates: tuple[date, ...]
    average_earliness: timedelta | None
    maximum_earliness: timedelta | None
    average_wake_to_first: timedelta | None
    minimum_wake_to_first: timedelta | None
    maximum_wake_to_first: timedelta | None
    actual_gaps: tuple[ActualGap, ...]
    average_actual_gap: timedelta | None
    longest_actual_gap: ActualGap | None
    last_sessions_before_next_wake: tuple[SmokingSession, ...]
    streak_at_end: int
    record_streak: int


def build_wake_cycles(
    wakes: tuple[WakeEvent, ...] | list[WakeEvent],
    sessions: tuple[SmokingSession, ...] | list[SmokingSession],
    *,
    timezone: ZoneInfo,
) -> tuple[WakeCycle, ...]:
    """Build primary-wake cycles; a session at the next wake belongs to the next cycle."""
    ordered_wakes = sorted(wakes, key=lambda wake: (wake.occurred_at, wake.id))
    ordered_sessions = sorted(sessions, key=lambda session: (session.occurred_at, session.id))

    wake_dates = [local_date_of(wake.occurred_at, timezone) for wake in ordered_wakes]
    if len(wake_dates) != len(set(wake_dates)):
        raise DuplicateWakeLocalDateError("Only one primary wake per local date is allowed")

    cycles: list[WakeCycle] = []
    for index, wake in enumerate(ordered_wakes):
        next_wake = ordered_wakes[index + 1] if index + 1 < len(ordered_wakes) else None
        bounded_sessions = [
            session
            for session in ordered_sessions
            if session.occurred_at >= wake.occurred_at
            and (next_wake is None or session.occurred_at < next_wake.occurred_at)
        ]
        cycles.append(
            WakeCycle(
                wake=wake,
                local_date=local_date_of(wake.occurred_at, timezone),
                next_wake=next_wake,
                first_session=bounded_sessions[0] if bounded_sessions else None,
                last_session_before_next_wake=(
                    bounded_sessions[-1] if bounded_sessions and next_wake is not None else None
                ),
            )
        )
    return tuple(cycles)


def actual_gaps_ending_in(timeline: Timeline, period: UtcPeriod) -> tuple[ActualGap, ...]:
    """Assign each completed gap to the period containing its ending session."""
    gaps: list[ActualGap] = []
    for previous, current in zip(timeline.sessions, timeline.sessions[1:], strict=False):
        if period.contains(current.session.occurred_at):
            gaps.append(
                ActualGap(
                    started_by=previous.session,
                    ended_by=current.session,
                    duration=current.session.occurred_at - previous.session.occurred_at,
                )
            )
    return tuple(gaps)


def build_daily_metrics(
    timeline: Timeline,
    wakes: tuple[WakeEvent, ...] | list[WakeEvent],
    *,
    local_date: date,
    activated_at: UtcInstant,
    timezone: ZoneInfo,
) -> DailyMetrics:
    """Build daily metrics, clipping the activation day to tracked time."""
    full_period = local_day_period(local_date, timezone)
    if activated_at >= full_period.end:
        raise ValueError("Cannot build metrics for a day before activation")
    effective_start = max(full_period.start, activated_at)
    period = UtcPeriod(start=effective_start, end=full_period.end)
    assessments = tuple(
        assessment
        for assessment in timeline.sessions
        if period.contains(assessment.session.occurred_at)
    )
    violations = tuple(assessment for assessment in assessments if assessment.is_violation)
    earliness_values = tuple(assessment.earliness for assessment in violations)

    raw_sessions = tuple(assessment.session for assessment in timeline.sessions)
    cycles = build_wake_cycles(wakes, raw_sessions, timezone=timezone)
    cycle = next(
        (
            candidate
            for candidate in cycles
            if candidate.local_date == local_date and candidate.wake.occurred_at >= effective_start
        ),
        None,
    )
    wake_metric = _wake_metric(cycle)
    streaks = calculate_completed_streaks(
        timeline,
        activated_at=activated_at,
        through_local_date=local_date,
        timezone=timezone,
    )

    return DailyMetrics(
        local_date=local_date,
        period=period,
        is_partial=effective_start > full_period.start,
        session_count=len(assessments),
        classifiable_session_count=sum(
            assessment.target_at is not None for assessment in assessments
        ),
        violation_count=len(violations),
        average_earliness=_average_duration(earliness_values),
        maximum_earliness=max(earliness_values, default=None),
        wake_to_first=wake_metric,
        streak_at_end=streaks.current,
    )


def build_weekly_metrics(
    timeline: Timeline,
    wakes: tuple[WakeEvent, ...] | list[WakeEvent],
    *,
    schedule: WeeklyReportSchedule,
    activated_at: UtcInstant,
    timezone: ZoneInfo,
) -> WeeklyMetrics:
    """Aggregate all agreed weekly metrics over a completed report schedule."""
    dates = tuple(_dates_in_schedule(schedule))
    days = tuple(
        build_daily_metrics(
            timeline,
            wakes,
            local_date=local_date,
            activated_at=activated_at,
            timezone=timezone,
        )
        for local_date in dates
    )
    if not days:
        raise ValueError("Weekly schedule must touch at least one local day")

    assessments = tuple(
        assessment
        for assessment in timeline.sessions
        if schedule.period.contains(assessment.session.occurred_at)
    )
    violations = tuple(assessment for assessment in assessments if assessment.is_violation)
    earliness_values = tuple(assessment.earliness for assessment in violations)
    measured_wakes = tuple(
        day.wake_to_first.duration
        for day in days
        if day.wake_to_first.status is WakeToFirstStatus.MEASURED
        and day.wake_to_first.duration is not None
    )
    gaps = actual_gaps_ending_in(timeline, schedule.period)
    longest_gap = max(gaps, key=lambda gap: gap.duration, default=None)

    raw_sessions = tuple(assessment.session for assessment in timeline.sessions)
    cycles = build_wake_cycles(wakes, raw_sessions, timezone=timezone)
    last_sessions = tuple(
        cycle.last_session_before_next_wake
        for cycle in cycles
        if schedule.period.contains(cycle.wake.occurred_at)
        and cycle.last_session_before_next_wake is not None
    )

    streaks = calculate_completed_streaks(
        timeline,
        activated_at=activated_at,
        through_local_date=schedule.end_local_date_exclusive - timedelta(days=1),
        timezone=timezone,
    )
    minimum_sessions, minimum_session_dates = _daily_extreme(days, "session_count", maximum=False)
    maximum_sessions, maximum_session_dates = _daily_extreme(days, "session_count", maximum=True)
    minimum_violations, minimum_violation_dates = _daily_extreme(
        days, "violation_count", maximum=False
    )
    maximum_violations, maximum_violation_dates = _daily_extreme(
        days, "violation_count", maximum=True
    )
    classifiable_count = sum(assessment.target_at is not None for assessment in assessments)

    return WeeklyMetrics(
        schedule=schedule,
        days=days,
        total_sessions=len(assessments),
        average_sessions_per_day=Fraction(len(assessments), len(days)),
        minimum_session_count=minimum_sessions,
        minimum_session_dates=minimum_session_dates,
        maximum_session_count=maximum_sessions,
        maximum_session_dates=maximum_session_dates,
        total_violations=len(violations),
        classifiable_session_count=classifiable_count,
        violation_rate=(
            Fraction(len(violations), classifiable_count) if classifiable_count else None
        ),
        minimum_violation_count=minimum_violations,
        minimum_violation_dates=minimum_violation_dates,
        maximum_violation_count=maximum_violations,
        maximum_violation_dates=maximum_violation_dates,
        average_earliness=_average_duration(earliness_values),
        maximum_earliness=max(earliness_values, default=None),
        average_wake_to_first=_average_duration(measured_wakes),
        minimum_wake_to_first=min(measured_wakes, default=None),
        maximum_wake_to_first=max(measured_wakes, default=None),
        actual_gaps=gaps,
        average_actual_gap=_average_duration(tuple(gap.duration for gap in gaps)),
        longest_actual_gap=longest_gap,
        last_sessions_before_next_wake=last_sessions,
        streak_at_end=streaks.current,
        record_streak=streaks.record,
    )


def weekly_totals_are_comparable(current: WeeklyMetrics, previous: WeeklyMetrics) -> bool:
    """Totals are comparable only when both periods contain seven full days."""
    return (
        not current.schedule.is_partial
        and not previous.schedule.is_partial
        and current.schedule.day_count == previous.schedule.day_count == 7
    )


def _wake_metric(cycle: WakeCycle | None) -> WakeToFirstMetric:
    if cycle is None:
        return WakeToFirstMetric(
            status=WakeToFirstStatus.MISSING_WAKE,
            wake=None,
            first_session=None,
            duration=None,
        )
    if cycle.first_session is None:
        return WakeToFirstMetric(
            status=WakeToFirstStatus.NO_SESSION_AFTER_WAKE,
            wake=cycle.wake,
            first_session=None,
            duration=None,
        )
    return WakeToFirstMetric(
        status=WakeToFirstStatus.MEASURED,
        wake=cycle.wake,
        first_session=cycle.first_session,
        duration=cycle.wake_to_first,
    )


def _average_duration(values: tuple[timedelta, ...]) -> timedelta | None:
    if not values:
        return None
    return sum(values, start=timedelta(0)) / len(values)


def _dates_in_schedule(schedule: WeeklyReportSchedule) -> tuple[date, ...]:
    return tuple(
        schedule.start_local_date + timedelta(days=offset) for offset in range(schedule.day_count)
    )


def _daily_extreme(
    days: tuple[DailyMetrics, ...],
    attribute: str,
    *,
    maximum: bool,
) -> tuple[int, tuple[date, ...]]:
    values = tuple(int(getattr(day, attribute)) for day in days)
    extreme = max(values) if maximum else min(values)
    dates = tuple(day.local_date for day in days if int(getattr(day, attribute)) == extreme)
    return extreme, dates
