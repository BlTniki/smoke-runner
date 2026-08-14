"""Immutable, serializable facts for deterministic report rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Literal

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.metrics import (
    DailyMetrics,
    WakeToFirstStatus,
    WeeklyMetrics,
    weekly_totals_are_comparable,
)


class ReportType(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


class ComparisonOutcome(StrEnum):
    IMPROVED = "improved"
    WORSENED = "worsened"
    UNCHANGED = "unchanged"
    NOT_COMPARABLE = "not_comparable"


class ComparisonUnavailableReason(StrEnum):
    PREVIOUS_PERIOD_UNAVAILABLE = "previous_period_unavailable"
    CURRENT_VALUE_MISSING = "current_value_missing"
    PREVIOUS_VALUE_MISSING = "previous_value_missing"
    BOTH_VALUES_MISSING = "both_values_missing"
    PARTIAL_PERIOD = "partial_period"


class HighlightKind(StrEnum):
    FEWER_VIOLATIONS = "fewer_violations"
    LONGER_WAKE_TO_FIRST = "longer_wake_to_first"
    FEWER_SESSIONS = "fewer_sessions"
    LOWER_AVERAGE_EARLINESS = "lower_average_earliness"
    LONGER_STREAK = "longer_streak"
    LONGEST_ACTUAL_GAP = "longest_actual_gap"
    HONEST_TRACKING = "honest_tracking"


@dataclass(frozen=True, slots=True)
class DurationValue:
    """Exact duration representation that remains JSON-safe through ``asdict``."""

    microseconds: int

    @classmethod
    def from_timedelta(cls, duration: timedelta) -> DurationValue:
        microseconds = (
            duration.days * 24 * 60 * 60 * 1_000_000
            + duration.seconds * 1_000_000
            + duration.microseconds
        )
        return cls(microseconds=microseconds)


@dataclass(frozen=True, slots=True)
class FractionValue:
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.denominator <= 0:
            raise ValueError("Fraction denominator must be positive")


@dataclass(frozen=True, slots=True)
class PeriodValue:
    start_at_utc: int
    end_at_utc: int


@dataclass(frozen=True, slots=True)
class WakeMetricValue:
    status: WakeToFirstStatus
    duration: DurationValue | None


@dataclass(frozen=True, slots=True)
class MetricComparison:
    outcome: ComparisonOutcome
    delta: int | None
    unavailable_reason: ComparisonUnavailableReason | None


@dataclass(frozen=True, slots=True)
class Highlight:
    kind: HighlightKind
    delta: int | None = None


@dataclass(frozen=True, slots=True)
class DailyMetricValues:
    local_date: str
    is_partial: bool
    session_count: int
    classifiable_session_count: int
    violation_count: int
    average_earliness: DurationValue | None
    maximum_earliness: DurationValue | None
    wake_to_first: WakeMetricValue
    streak_at_end: int


@dataclass(frozen=True, slots=True)
class DailyComparisons:
    session_count: MetricComparison
    violation_count: MetricComparison
    average_earliness: MetricComparison
    wake_to_first: MetricComparison
    streak_at_end: MetricComparison


@dataclass(frozen=True, slots=True)
class DailyReportSnapshot:
    schema_version: Literal[1]
    report_type: Literal[ReportType.DAILY]
    generated_at_utc: int
    timezone_name: str
    period: PeriodValue
    current: DailyMetricValues
    previous: DailyMetricValues | None
    comparisons: DailyComparisons
    highlight: Highlight


@dataclass(frozen=True, slots=True)
class DailyChartPoint:
    local_date: str
    is_partial: bool
    session_count: int
    violation_count: int
    average_earliness: DurationValue | None
    wake_to_first: WakeMetricValue


@dataclass(frozen=True, slots=True)
class DailyCountExtreme:
    value: int
    local_dates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LongestGapValue:
    started_at_utc: int
    ended_at_utc: int
    duration: DurationValue


@dataclass(frozen=True, slots=True)
class WeeklyMetricValues:
    period: PeriodValue
    start_local_date: str
    end_local_date_exclusive: str
    is_partial: bool
    first_day_is_partial: bool
    day_count: int
    total_sessions: int
    average_sessions_per_day: FractionValue
    minimum_sessions: DailyCountExtreme
    maximum_sessions: DailyCountExtreme
    total_violations: int
    classifiable_session_count: int
    violation_rate: FractionValue | None
    minimum_violations: DailyCountExtreme
    maximum_violations: DailyCountExtreme
    average_earliness: DurationValue | None
    maximum_earliness: DurationValue | None
    average_wake_to_first: DurationValue | None
    minimum_wake_to_first: DurationValue | None
    maximum_wake_to_first: DurationValue | None
    average_actual_gap: DurationValue | None
    longest_actual_gap: LongestGapValue | None
    last_sessions_before_next_wake_at_utc: tuple[int, ...]
    streak_at_end: int
    record_streak: int


@dataclass(frozen=True, slots=True)
class WeeklyComparisons:
    total_sessions: MetricComparison
    total_violations: MetricComparison
    average_earliness: MetricComparison
    average_wake_to_first: MetricComparison
    streak_at_end: MetricComparison


@dataclass(frozen=True, slots=True)
class WeeklyChartPoint:
    start_local_date: str
    end_local_date_exclusive: str
    is_partial: bool
    total_sessions: int
    total_violations: int
    average_earliness: DurationValue | None
    average_wake_to_first: DurationValue | None


@dataclass(frozen=True, slots=True)
class WeeklyReportSnapshot:
    schema_version: Literal[1]
    report_type: Literal[ReportType.WEEKLY]
    generated_at_utc: int
    timezone_name: str
    period: PeriodValue
    current: WeeklyMetricValues
    previous: WeeklyMetricValues | None
    comparisons: WeeklyComparisons
    current_week_chart: tuple[DailyChartPoint, ...]
    history_chart: tuple[WeeklyChartPoint, ...]
    highlight: Highlight


type ReportMetricValues = DailyMetricValues | WeeklyMetricValues
type ReportComparisons = DailyComparisons | WeeklyComparisons


@dataclass(frozen=True, slots=True)
class ReportCommentaryInput:
    """Privacy-minimized, provider-independent input for future AI commentary."""

    schema_version: Literal[1]
    report_type: ReportType
    timezone_name: str
    period: PeriodValue
    metrics: ReportMetricValues
    comparisons: ReportComparisons
    interesting_facts: tuple[Highlight, ...]


def build_daily_report_snapshot(
    current: DailyMetrics,
    *,
    previous: DailyMetrics | None,
    generated_at: UtcInstant,
    timezone_name: str,
) -> DailyReportSnapshot:
    current_values = _daily_values(current)
    previous_values = _daily_values(previous) if previous is not None else None
    comparisons = _daily_comparisons(current_values, previous_values)
    return DailyReportSnapshot(
        schema_version=1,
        report_type=ReportType.DAILY,
        generated_at_utc=generated_at.to_unix_seconds(),
        timezone_name=timezone_name,
        period=_period_value(current.period.start, current.period.end),
        current=current_values,
        previous=previous_values,
        comparisons=comparisons,
        highlight=_daily_highlight(comparisons),
    )


def build_weekly_report_snapshot(
    current: WeeklyMetrics,
    *,
    previous: WeeklyMetrics | None,
    history: tuple[WeeklyMetrics, ...] | list[WeeklyMetrics],
    generated_at: UtcInstant,
    timezone_name: str,
) -> WeeklyReportSnapshot:
    current_values = _weekly_values(current)
    previous_values = _weekly_values(previous) if previous is not None else None
    comparisons = _weekly_comparisons(current, previous, current_values, previous_values)
    return WeeklyReportSnapshot(
        schema_version=1,
        report_type=ReportType.WEEKLY,
        generated_at_utc=generated_at.to_unix_seconds(),
        timezone_name=timezone_name,
        period=current_values.period,
        current=current_values,
        previous=previous_values,
        comparisons=comparisons,
        current_week_chart=tuple(_daily_chart_point(day) for day in current.days),
        history_chart=tuple(
            _weekly_chart_point(metrics)
            for metrics in sorted(
                history,
                key=lambda metrics: metrics.schedule.period.start,
            )
        ),
        highlight=_weekly_highlight(comparisons, current_values),
    )


def commentary_input_from_snapshot(
    snapshot: DailyReportSnapshot | WeeklyReportSnapshot,
) -> ReportCommentaryInput:
    return ReportCommentaryInput(
        schema_version=1,
        report_type=ReportType(snapshot.report_type),
        timezone_name=snapshot.timezone_name,
        period=snapshot.period,
        metrics=snapshot.current,
        comparisons=snapshot.comparisons,
        interesting_facts=(snapshot.highlight,),
    )


def _daily_values(metrics: DailyMetrics) -> DailyMetricValues:
    return DailyMetricValues(
        local_date=metrics.local_date.isoformat(),
        is_partial=metrics.is_partial,
        session_count=metrics.session_count,
        classifiable_session_count=metrics.classifiable_session_count,
        violation_count=metrics.violation_count,
        average_earliness=_optional_duration(metrics.average_earliness),
        maximum_earliness=_optional_duration(metrics.maximum_earliness),
        wake_to_first=WakeMetricValue(
            status=metrics.wake_to_first.status,
            duration=_optional_duration(metrics.wake_to_first.duration),
        ),
        streak_at_end=metrics.streak_at_end,
    )


def _weekly_values(metrics: WeeklyMetrics) -> WeeklyMetricValues:
    longest_gap = metrics.longest_actual_gap
    return WeeklyMetricValues(
        period=_period_value(metrics.schedule.period.start, metrics.schedule.period.end),
        start_local_date=metrics.schedule.start_local_date.isoformat(),
        end_local_date_exclusive=metrics.schedule.end_local_date_exclusive.isoformat(),
        is_partial=metrics.schedule.is_partial,
        first_day_is_partial=metrics.schedule.first_day_is_partial,
        day_count=metrics.schedule.day_count,
        total_sessions=metrics.total_sessions,
        average_sessions_per_day=FractionValue(
            numerator=metrics.average_sessions_per_day.numerator,
            denominator=metrics.average_sessions_per_day.denominator,
        ),
        minimum_sessions=DailyCountExtreme(
            value=metrics.minimum_session_count,
            local_dates=tuple(value.isoformat() for value in metrics.minimum_session_dates),
        ),
        maximum_sessions=DailyCountExtreme(
            value=metrics.maximum_session_count,
            local_dates=tuple(value.isoformat() for value in metrics.maximum_session_dates),
        ),
        total_violations=metrics.total_violations,
        classifiable_session_count=metrics.classifiable_session_count,
        violation_rate=(
            FractionValue(
                numerator=metrics.violation_rate.numerator,
                denominator=metrics.violation_rate.denominator,
            )
            if metrics.violation_rate is not None
            else None
        ),
        minimum_violations=DailyCountExtreme(
            value=metrics.minimum_violation_count,
            local_dates=tuple(value.isoformat() for value in metrics.minimum_violation_dates),
        ),
        maximum_violations=DailyCountExtreme(
            value=metrics.maximum_violation_count,
            local_dates=tuple(value.isoformat() for value in metrics.maximum_violation_dates),
        ),
        average_earliness=_optional_duration(metrics.average_earliness),
        maximum_earliness=_optional_duration(metrics.maximum_earliness),
        average_wake_to_first=_optional_duration(metrics.average_wake_to_first),
        minimum_wake_to_first=_optional_duration(metrics.minimum_wake_to_first),
        maximum_wake_to_first=_optional_duration(metrics.maximum_wake_to_first),
        average_actual_gap=_optional_duration(metrics.average_actual_gap),
        longest_actual_gap=(
            LongestGapValue(
                started_at_utc=longest_gap.started_by.occurred_at.to_unix_seconds(),
                ended_at_utc=longest_gap.ended_by.occurred_at.to_unix_seconds(),
                duration=DurationValue.from_timedelta(longest_gap.duration),
            )
            if longest_gap is not None
            else None
        ),
        last_sessions_before_next_wake_at_utc=tuple(
            session.occurred_at.to_unix_seconds()
            for session in metrics.last_sessions_before_next_wake
        ),
        streak_at_end=metrics.streak_at_end,
        record_streak=metrics.record_streak,
    )


def _daily_comparisons(
    current: DailyMetricValues,
    previous: DailyMetricValues | None,
) -> DailyComparisons:
    if previous is None:
        unavailable = _unavailable_comparison(
            ComparisonUnavailableReason.PREVIOUS_PERIOD_UNAVAILABLE
        )
        return DailyComparisons(
            session_count=unavailable,
            violation_count=unavailable,
            average_earliness=unavailable,
            wake_to_first=unavailable,
            streak_at_end=unavailable,
        )
    return DailyComparisons(
        session_count=_compare_values(
            current.session_count,
            previous.session_count,
            higher_is_better=False,
        ),
        violation_count=_compare_values(
            current.violation_count,
            previous.violation_count,
            higher_is_better=False,
        ),
        average_earliness=_compare_values(
            _duration_number(current.average_earliness),
            _duration_number(previous.average_earliness),
            higher_is_better=False,
        ),
        wake_to_first=_compare_values(
            _duration_number(current.wake_to_first.duration),
            _duration_number(previous.wake_to_first.duration),
            higher_is_better=True,
        ),
        streak_at_end=_compare_values(
            current.streak_at_end,
            previous.streak_at_end,
            higher_is_better=True,
        ),
    )


def _weekly_comparisons(
    current_metrics: WeeklyMetrics,
    previous_metrics: WeeklyMetrics | None,
    current: WeeklyMetricValues,
    previous: WeeklyMetricValues | None,
) -> WeeklyComparisons:
    if previous_metrics is None or previous is None:
        unavailable = _unavailable_comparison(
            ComparisonUnavailableReason.PREVIOUS_PERIOD_UNAVAILABLE
        )
        return WeeklyComparisons(
            total_sessions=unavailable,
            total_violations=unavailable,
            average_earliness=unavailable,
            average_wake_to_first=unavailable,
            streak_at_end=unavailable,
        )
    if not weekly_totals_are_comparable(current_metrics, previous_metrics):
        unavailable = _unavailable_comparison(ComparisonUnavailableReason.PARTIAL_PERIOD)
        return WeeklyComparisons(
            total_sessions=unavailable,
            total_violations=unavailable,
            average_earliness=unavailable,
            average_wake_to_first=unavailable,
            streak_at_end=unavailable,
        )
    return WeeklyComparisons(
        total_sessions=_compare_values(
            current.total_sessions,
            previous.total_sessions,
            higher_is_better=False,
        ),
        total_violations=_compare_values(
            current.total_violations,
            previous.total_violations,
            higher_is_better=False,
        ),
        average_earliness=_compare_values(
            _duration_number(current.average_earliness),
            _duration_number(previous.average_earliness),
            higher_is_better=False,
        ),
        average_wake_to_first=_compare_values(
            _duration_number(current.average_wake_to_first),
            _duration_number(previous.average_wake_to_first),
            higher_is_better=True,
        ),
        streak_at_end=_compare_values(
            current.streak_at_end,
            previous.streak_at_end,
            higher_is_better=True,
        ),
    )


def _compare_values(
    current: int | None,
    previous: int | None,
    *,
    higher_is_better: bool,
) -> MetricComparison:
    if current is None or previous is None:
        if current is None and previous is None:
            reason = ComparisonUnavailableReason.BOTH_VALUES_MISSING
        elif current is None:
            reason = ComparisonUnavailableReason.CURRENT_VALUE_MISSING
        else:
            reason = ComparisonUnavailableReason.PREVIOUS_VALUE_MISSING
        return _unavailable_comparison(reason)

    delta = current - previous
    if delta == 0:
        outcome = ComparisonOutcome.UNCHANGED
    elif (delta > 0) is higher_is_better:
        outcome = ComparisonOutcome.IMPROVED
    else:
        outcome = ComparisonOutcome.WORSENED
    return MetricComparison(outcome=outcome, delta=delta, unavailable_reason=None)


def _unavailable_comparison(reason: ComparisonUnavailableReason) -> MetricComparison:
    return MetricComparison(
        outcome=ComparisonOutcome.NOT_COMPARABLE,
        delta=None,
        unavailable_reason=reason,
    )


def _daily_highlight(comparisons: DailyComparisons) -> Highlight:
    ranked = (
        (HighlightKind.FEWER_VIOLATIONS, comparisons.violation_count),
        (HighlightKind.LONGER_WAKE_TO_FIRST, comparisons.wake_to_first),
        (HighlightKind.FEWER_SESSIONS, comparisons.session_count),
        (HighlightKind.LOWER_AVERAGE_EARLINESS, comparisons.average_earliness),
        (HighlightKind.LONGER_STREAK, comparisons.streak_at_end),
    )
    return _first_improvement_or_honest_tracking(ranked)


def _weekly_highlight(
    comparisons: WeeklyComparisons,
    current: WeeklyMetricValues,
) -> Highlight:
    ranked = (
        (HighlightKind.FEWER_VIOLATIONS, comparisons.total_violations),
        (HighlightKind.LONGER_WAKE_TO_FIRST, comparisons.average_wake_to_first),
        (HighlightKind.FEWER_SESSIONS, comparisons.total_sessions),
        (HighlightKind.LOWER_AVERAGE_EARLINESS, comparisons.average_earliness),
        (HighlightKind.LONGER_STREAK, comparisons.streak_at_end),
    )
    improvement = _first_improvement_or_honest_tracking(ranked)
    if improvement.kind is not HighlightKind.HONEST_TRACKING:
        return improvement
    if current.longest_actual_gap is not None:
        return Highlight(
            kind=HighlightKind.LONGEST_ACTUAL_GAP,
            delta=current.longest_actual_gap.duration.microseconds,
        )
    return improvement


def _first_improvement_or_honest_tracking(
    ranked: tuple[tuple[HighlightKind, MetricComparison], ...],
) -> Highlight:
    for kind, comparison in ranked:
        if comparison.outcome is ComparisonOutcome.IMPROVED:
            return Highlight(kind=kind, delta=comparison.delta)
    return Highlight(kind=HighlightKind.HONEST_TRACKING)


def _daily_chart_point(metrics: DailyMetrics) -> DailyChartPoint:
    values = _daily_values(metrics)
    return DailyChartPoint(
        local_date=values.local_date,
        is_partial=values.is_partial,
        session_count=values.session_count,
        violation_count=values.violation_count,
        average_earliness=values.average_earliness,
        wake_to_first=values.wake_to_first,
    )


def _weekly_chart_point(metrics: WeeklyMetrics) -> WeeklyChartPoint:
    values = _weekly_values(metrics)
    return WeeklyChartPoint(
        start_local_date=values.start_local_date,
        end_local_date_exclusive=values.end_local_date_exclusive,
        is_partial=values.is_partial,
        total_sessions=values.total_sessions,
        total_violations=values.total_violations,
        average_earliness=values.average_earliness,
        average_wake_to_first=values.average_wake_to_first,
    )


def _period_value(start: UtcInstant, end: UtcInstant) -> PeriodValue:
    return PeriodValue(
        start_at_utc=start.to_unix_seconds(),
        end_at_utc=end.to_unix_seconds(),
    )


def _optional_duration(duration: timedelta | None) -> DurationValue | None:
    return DurationValue.from_timedelta(duration) if duration is not None else None


def _duration_number(duration: DurationValue | None) -> int | None:
    return duration.microseconds if duration is not None else None
