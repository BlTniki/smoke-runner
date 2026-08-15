# ruff: noqa: RUF001
"""Deterministic Russian report text and phone-readable PNG charts."""

from __future__ import annotations

import io
import math
import threading
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from smoke_runner.domain.metrics import WakeToFirstStatus
from smoke_runner.domain.report_models import (
    ComparisonOutcome,
    ComparisonUnavailableReason,
    DailyChartPoint,
    DailyComparisons,
    DailyCountExtreme,
    DailyMetricValues,
    DailyReportSnapshot,
    DurationValue,
    FractionValue,
    Highlight,
    HighlightKind,
    LongestGapValue,
    MetricComparison,
    PeriodValue,
    ReportType,
    WakeMetricValue,
    WeeklyChartPoint,
    WeeklyComparisons,
    WeeklyMetricValues,
    WeeklyReportSnapshot,
)

_PLOT_LOCK = threading.Lock()
_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def render_report_text(
    snapshot: DailyReportSnapshot | WeeklyReportSnapshot,
    *,
    commentary: str | None = None,
) -> str:
    if isinstance(snapshot, DailyReportSnapshot):
        text = _render_daily(snapshot)
    else:
        text = _render_weekly(snapshot)
    if commentary:
        text += f"\n\n💬 Наблюдение помощника\n{commentary.strip()}"
    return text


def render_current_week_chart(snapshot: WeeklyReportSnapshot) -> bytes:
    labels = [_day_label(point.local_date) for point in snapshot.current_week_chart]
    series: tuple[tuple[str, list[float | None]], ...] = (
        ("Эпизоды", [float(point.session_count) for point in snapshot.current_week_chart]),
        ("Нарушения", [float(point.violation_count) for point in snapshot.current_week_chart]),
        (
            "Раньше рубежа, мин",
            [_duration_minutes(point.average_earliness) for point in snapshot.current_week_chart],
        ),
        (
            "От пробуждения, мин",
            [
                _duration_minutes(point.wake_to_first.duration)
                for point in snapshot.current_week_chart
            ],
        ),
    )
    partial = [point.is_partial for point in snapshot.current_week_chart]
    return _render_panels(
        title="Текущая неделя по дням",
        labels=labels,
        series=series,
        partial=partial,
    )


def render_history_chart(snapshot: WeeklyReportSnapshot) -> bytes:
    labels = [_week_label(point) for point in snapshot.history_chart]
    series: tuple[tuple[str, list[float | None]], ...] = (
        ("Эпизоды", [float(point.total_sessions) for point in snapshot.history_chart]),
        ("Нарушения", [float(point.total_violations) for point in snapshot.history_chart]),
        (
            "Раньше рубежа, мин",
            [_duration_minutes(point.average_earliness) for point in snapshot.history_chart],
        ),
        (
            "От пробуждения, мин",
            [_duration_minutes(point.average_wake_to_first) for point in snapshot.history_chart],
        ),
    )
    partial = [point.is_partial for point in snapshot.history_chart]
    return _render_panels(
        title="Весь период по неделям",
        labels=labels,
        series=series,
        partial=partial,
    )


def deserialize_report_snapshot(
    payload: Mapping[str, Any],
) -> DailyReportSnapshot | WeeklyReportSnapshot:
    report_type = ReportType(str(payload["report_type"]))
    if report_type is ReportType.DAILY:
        return DailyReportSnapshot(
            schema_version=1,
            report_type=ReportType.DAILY,
            generated_at_utc=int(payload["generated_at_utc"]),
            timezone_name=str(payload["timezone_name"]),
            period=_period(_mapping(payload["period"])),
            current=_daily_values(_mapping(payload["current"])),
            previous=(
                None
                if payload.get("previous") is None
                else _daily_values(_mapping(payload["previous"]))
            ),
            comparisons=_daily_comparisons(_mapping(payload["comparisons"])),
            highlight=_highlight(_mapping(payload["highlight"])),
        )
    return WeeklyReportSnapshot(
        schema_version=1,
        report_type=ReportType.WEEKLY,
        generated_at_utc=int(payload["generated_at_utc"]),
        timezone_name=str(payload["timezone_name"]),
        period=_period(_mapping(payload["period"])),
        current=_weekly_values(_mapping(payload["current"])),
        previous=(
            None
            if payload.get("previous") is None
            else _weekly_values(_mapping(payload["previous"]))
        ),
        comparisons=_weekly_comparisons(_mapping(payload["comparisons"])),
        current_week_chart=tuple(
            _daily_chart_point(_mapping(point))
            for point in cast(list[Any], payload["current_week_chart"])
        ),
        history_chart=tuple(
            _weekly_chart_point(_mapping(point))
            for point in cast(list[Any], payload["history_chart"])
        ),
        highlight=_highlight(_mapping(payload["highlight"])),
    )


def _render_daily(snapshot: DailyReportSnapshot) -> str:
    current = snapshot.current
    suffix = " · неполный день" if current.is_partial else ""
    lines = [f"📊 Ежедневный отчёт · {_format_date(current.local_date)}{suffix}", ""]
    lines.extend(
        (
            _count_line("Эпизоды", current.session_count, snapshot.comparisons.session_count),
            _count_line("Нарушения", current.violation_count, snapshot.comparisons.violation_count),
            _duration_line(
                "Среднее опережение",
                current.average_earliness,
                snapshot.comparisons.average_earliness,
                empty="— (нарушений не было)",
            ),
            _wake_line(current.wake_to_first, snapshot.comparisons.wake_to_first),
            _count_line(
                "Серия без нарушений",
                current.streak_at_end,
                snapshot.comparisons.streak_at_end,
                unit="дн.",
            ),
            "",
            _highlight_text(snapshot.highlight),
            "По записанным данным — спасибо, что продолжаешь вести лог.",
        )
    )
    return "\n".join(lines)


def _render_weekly(snapshot: WeeklyReportSnapshot) -> str:
    current = snapshot.current
    first = date.fromisoformat(current.start_local_date)
    end_inclusive = date.fromisoformat(snapshot.current_week_chart[-1].local_date)
    partial = " · неполная неделя" if current.is_partial else ""
    average_sessions = (
        current.average_sessions_per_day.numerator / current.average_sessions_per_day.denominator
    )
    violation_rate = (
        "—"
        if current.violation_rate is None
        else f"{100 * current.violation_rate.numerator / current.violation_rate.denominator:.0f}%"
    )
    lines = [
        f"📈 Еженедельный отчёт · {first:%d.%m}–{end_inclusive:%d.%m.%Y}{partial}",
        f"Затронуто дней: {current.day_count}"
        + (" (первый день частичный)" if current.first_day_is_partial else ""),
        "",
        _count_line("Всего эпизодов", current.total_sessions, snapshot.comparisons.total_sessions),
        f"Среднее в день: {average_sessions:.1f}",
        _extreme_line("Минимум эпизодов", current.minimum_sessions),
        _extreme_line("Максимум эпизодов", current.maximum_sessions),
        "",
        _count_line(
            "Всего нарушений", current.total_violations, snapshot.comparisons.total_violations
        ),
        f"Доля среди классифицируемых эпизодов: {violation_rate}",
        _extreme_line("Минимум нарушений", current.minimum_violations),
        _extreme_line("Максимум нарушений", current.maximum_violations),
        _duration_line(
            "Среднее опережение",
            current.average_earliness,
            snapshot.comparisons.average_earliness,
            empty="— (нарушений не было)",
        ),
        f"Максимальное опережение: {_duration_text(current.maximum_earliness)}",
        "",
        _duration_line(
            "Среднее от пробуждения",
            current.average_wake_to_first,
            snapshot.comparisons.average_wake_to_first,
        ),
        f"Минимум от пробуждения: {_duration_text(current.minimum_wake_to_first)}",
        f"Максимум от пробуждения: {_duration_text(current.maximum_wake_to_first)}",
        "",
        f"Средний фактический интервал: {_duration_text(current.average_actual_gap)}",
        _longest_gap_line(current.longest_actual_gap, snapshot.timezone_name),
        _last_sessions_line(current.last_sessions_before_next_wake_at_utc, snapshot.timezone_name),
        f"Серия на конец недели: {current.streak_at_end} дн.",
        f"Рекордная серия: {current.record_streak} дн.",
        "",
        _highlight_text(snapshot.highlight),
        "По записанным данным — каждый честно отмеченный эпизод помогает видеть прогресс.",
    ]
    if current.is_partial:
        lines.insert(2, "Итоги неполной и полной недели напрямую не сравниваются.")
    return "\n".join(lines)


def _render_panels(
    *,
    title: str,
    labels: list[str],
    series: tuple[tuple[str, list[float | None]], ...],
    partial: list[bool],
) -> bytes:
    with _PLOT_LOCK:
        fig, axes = plt.subplots(2, 2, figsize=(7.2, 8.2), dpi=150)
        fig.suptitle(title, fontsize=16, fontweight="bold")
        x_values = list(range(len(labels)))
        tick_step = max(1, math.ceil(len(labels) / 8))
        for axis, (label, values) in zip(axes.flat, series, strict=True):
            plot_values = [float("nan") if value is None else value for value in values]
            axis.plot(x_values, plot_values, marker="o", linewidth=2, color="#5B6EE1")
            for index, is_partial in enumerate(partial):
                if is_partial:
                    axis.axvspan(index - 0.45, index + 0.45, color="#F7D774", alpha=0.35)
            missing = [index for index, value in enumerate(values) if value is None]
            if missing:
                axis.scatter(
                    missing,
                    [0.0] * len(missing),
                    marker="x",
                    color="#777777",
                    label="нет данных",
                    zorder=3,
                )
                axis.legend(loc="upper right", fontsize=8, frameon=False)
            axis.set_title(label, fontsize=11)
            axis.grid(axis="y", alpha=0.25)
            axis.set_xticks(x_values[::tick_step], labels[::tick_step], rotation=45, ha="right")
            axis.tick_params(labelsize=8)
            axis.set_ylim(bottom=0)
        fig.text(
            0.5,
            0.01,
            "Жёлтая область — неполный период · × — нет данных",
            ha="center",
            fontsize=8,
            color="#555555",
        )
        fig.tight_layout(rect=(0, 0.035, 1, 0.96))
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", bbox_inches="tight", facecolor="white")
        plt.close(fig)
        return buffer.getvalue()


def _count_line(
    label: str,
    value: int,
    comparison: MetricComparison,
    *,
    unit: str = "",
) -> str:
    suffix = f" {unit}" if unit else ""
    return f"{label}: {value}{suffix}{_comparison_text(comparison, duration=False)}"


def _duration_line(
    label: str,
    value: DurationValue | None,
    comparison: MetricComparison,
    *,
    empty: str = "— (нет сравнимых данных)",
) -> str:
    rendered = empty if value is None else _duration_text(value)
    return f"{label}: {rendered}{_comparison_text(comparison, duration=True)}"


def _wake_line(value: WakeMetricValue, comparison: MetricComparison) -> str:
    if value.status is WakeToFirstStatus.MISSING_WAKE:
        rendered = "— (пробуждение не записано)"
    elif value.status is WakeToFirstStatus.NO_SESSION_AFTER_WAKE:
        rendered = "после пробуждения не курил"
    else:
        rendered = _duration_text(value.duration)
    comparison_text = _comparison_text(comparison, duration=True)
    return f"От пробуждения до первого эпизода: {rendered}{comparison_text}"


def _comparison_text(comparison: MetricComparison, *, duration: bool) -> str:
    if comparison.outcome is ComparisonOutcome.NOT_COMPARABLE or comparison.delta is None:
        if comparison.unavailable_reason is ComparisonUnavailableReason.PREVIOUS_PERIOD_UNAVAILABLE:
            return " · сравнение пока недоступно"
        if comparison.unavailable_reason is ComparisonUnavailableReason.PARTIAL_PERIOD:
            return " · периоды несопоставимы"
        return " · нет сравнимых данных"
    if comparison.delta == 0:
        return " · без изменений"
    absolute = abs(comparison.delta)
    value = _duration_text(DurationValue(absolute)) if duration else str(absolute)
    direction = "больше" if comparison.delta > 0 else "меньше"
    marker = "лучше" if comparison.outcome is ComparisonOutcome.IMPROVED else ""
    return f" · на {value} {direction}" + (f" ({marker})" if marker else "")


def _extreme_line(label: str, extreme: DailyCountExtreme) -> str:
    dates = ", ".join(_day_label(value) for value in extreme.local_dates)
    return f"{label}: {extreme.value} ({dates})"


def _longest_gap_line(value: LongestGapValue | None, timezone_name: str) -> str:
    if value is None:
        return "Самый длинный фактический интервал: —"
    timezone = ZoneInfo(timezone_name)
    started = datetime.fromtimestamp(value.started_at_utc, tz=UTC).astimezone(timezone)
    ended = datetime.fromtimestamp(value.ended_at_utc, tz=UTC).astimezone(timezone)
    return (
        f"Самый длинный фактический интервал: {_duration_text(value.duration)} "
        f"({started:%d.%m %H:%M} → {ended:%d.%m %H:%M})"
    )


def _last_sessions_line(values: tuple[int, ...], timezone_name: str) -> str:
    if not values:
        return "Последние эпизоды перед следующим пробуждением: —"
    timezone = ZoneInfo(timezone_name)
    rendered = ", ".join(
        datetime.fromtimestamp(value, tz=UTC).astimezone(timezone).strftime("%d.%m %H:%M")
        for value in values
    )
    return f"Последние эпизоды перед следующим пробуждением: {rendered}"


def _highlight_text(highlight: Highlight) -> str:
    messages = {
        HighlightKind.FEWER_VIOLATIONS: "🌿 Круто: нарушений стало меньше.",
        HighlightKind.LONGER_WAKE_TO_FIRST: (
            "🌿 Круто: утром получилось дольше обходиться без вейпа."
        ),
        HighlightKind.FEWER_SESSIONS: "🌿 Круто: эпизодов стало меньше.",
        HighlightKind.LOWER_AVERAGE_EARLINESS: "🌿 Круто: ранние отклонения стали мягче.",
        HighlightKind.LONGER_STREAK: "🌿 Круто: серия без нарушений выросла.",
        HighlightKind.LONGEST_ACTUAL_GAP: "🌿 Отличная пауза — это заметный результат.",
        HighlightKind.HONEST_TRACKING: (
            "🌿 Ты продолжаешь честно вести лог — это уже важная работа."
        ),
    }
    return messages[highlight.kind]


def _duration_text(value: DurationValue | None) -> str:
    if value is None:
        return "—"
    total_minutes = max(0, round(value.microseconds / 60_000_000))
    days, remaining = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining, 60)
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч")
    if minutes or not parts:
        parts.append(f"{minutes} мин")
    return " ".join(parts)


def _duration_minutes(value: DurationValue | None) -> float | None:
    return None if value is None else value.microseconds / 60_000_000


def _format_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def _day_label(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{_WEEKDAYS[parsed.weekday()]} {parsed:%d.%m}"


def _week_label(point: WeeklyChartPoint) -> str:
    start = date.fromisoformat(point.start_local_date)
    return f"{start:%d.%m}" + ("*" if point.is_partial else "")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object in report snapshot")
    return cast(Mapping[str, Any], value)


def _duration(value: Any) -> DurationValue | None:
    if value is None:
        return None
    return DurationValue(microseconds=int(_mapping(value)["microseconds"]))


def _period(value: Mapping[str, Any]) -> PeriodValue:
    return PeriodValue(int(value["start_at_utc"]), int(value["end_at_utc"]))


def _fraction(value: Any) -> FractionValue | None:
    if value is None:
        return None
    mapped = _mapping(value)
    return FractionValue(int(mapped["numerator"]), int(mapped["denominator"]))


def _wake(value: Mapping[str, Any]) -> WakeMetricValue:
    return WakeMetricValue(
        status=WakeToFirstStatus(str(value["status"])),
        duration=_duration(value.get("duration")),
    )


def _comparison(value: Mapping[str, Any]) -> MetricComparison:
    reason = value.get("unavailable_reason")
    return MetricComparison(
        outcome=ComparisonOutcome(str(value["outcome"])),
        delta=None if value.get("delta") is None else int(value["delta"]),
        unavailable_reason=(None if reason is None else ComparisonUnavailableReason(str(reason))),
    )


def _highlight(value: Mapping[str, Any]) -> Highlight:
    return Highlight(
        kind=HighlightKind(str(value["kind"])),
        delta=None if value.get("delta") is None else int(value["delta"]),
    )


def _daily_values(value: Mapping[str, Any]) -> DailyMetricValues:
    return DailyMetricValues(
        local_date=str(value["local_date"]),
        is_partial=bool(value["is_partial"]),
        session_count=int(value["session_count"]),
        classifiable_session_count=int(value["classifiable_session_count"]),
        violation_count=int(value["violation_count"]),
        average_earliness=_duration(value.get("average_earliness")),
        maximum_earliness=_duration(value.get("maximum_earliness")),
        wake_to_first=_wake(_mapping(value["wake_to_first"])),
        streak_at_end=int(value["streak_at_end"]),
    )


def _daily_comparisons(value: Mapping[str, Any]) -> DailyComparisons:
    return DailyComparisons(
        session_count=_comparison(_mapping(value["session_count"])),
        violation_count=_comparison(_mapping(value["violation_count"])),
        average_earliness=_comparison(_mapping(value["average_earliness"])),
        wake_to_first=_comparison(_mapping(value["wake_to_first"])),
        streak_at_end=_comparison(_mapping(value["streak_at_end"])),
    )


def _extreme(value: Mapping[str, Any]) -> DailyCountExtreme:
    return DailyCountExtreme(
        value=int(value["value"]),
        local_dates=tuple(str(item) for item in cast(list[Any], value["local_dates"])),
    )


def _longest_gap(value: Any) -> LongestGapValue | None:
    if value is None:
        return None
    mapped = _mapping(value)
    duration = _duration(mapped["duration"])
    assert duration is not None
    return LongestGapValue(
        started_at_utc=int(mapped["started_at_utc"]),
        ended_at_utc=int(mapped["ended_at_utc"]),
        duration=duration,
    )


def _weekly_values(value: Mapping[str, Any]) -> WeeklyMetricValues:
    average_sessions = _fraction(value["average_sessions_per_day"])
    assert average_sessions is not None
    return WeeklyMetricValues(
        period=_period(_mapping(value["period"])),
        start_local_date=str(value["start_local_date"]),
        end_local_date_exclusive=str(value["end_local_date_exclusive"]),
        is_partial=bool(value["is_partial"]),
        first_day_is_partial=bool(value["first_day_is_partial"]),
        day_count=int(value["day_count"]),
        total_sessions=int(value["total_sessions"]),
        average_sessions_per_day=average_sessions,
        minimum_sessions=_extreme(_mapping(value["minimum_sessions"])),
        maximum_sessions=_extreme(_mapping(value["maximum_sessions"])),
        total_violations=int(value["total_violations"]),
        classifiable_session_count=int(value["classifiable_session_count"]),
        violation_rate=_fraction(value.get("violation_rate")),
        minimum_violations=_extreme(_mapping(value["minimum_violations"])),
        maximum_violations=_extreme(_mapping(value["maximum_violations"])),
        average_earliness=_duration(value.get("average_earliness")),
        maximum_earliness=_duration(value.get("maximum_earliness")),
        average_wake_to_first=_duration(value.get("average_wake_to_first")),
        minimum_wake_to_first=_duration(value.get("minimum_wake_to_first")),
        maximum_wake_to_first=_duration(value.get("maximum_wake_to_first")),
        average_actual_gap=_duration(value.get("average_actual_gap")),
        longest_actual_gap=_longest_gap(value.get("longest_actual_gap")),
        last_sessions_before_next_wake_at_utc=tuple(
            int(item) for item in cast(list[Any], value["last_sessions_before_next_wake_at_utc"])
        ),
        streak_at_end=int(value["streak_at_end"]),
        record_streak=int(value["record_streak"]),
    )


def _weekly_comparisons(value: Mapping[str, Any]) -> WeeklyComparisons:
    return WeeklyComparisons(
        total_sessions=_comparison(_mapping(value["total_sessions"])),
        total_violations=_comparison(_mapping(value["total_violations"])),
        average_earliness=_comparison(_mapping(value["average_earliness"])),
        average_wake_to_first=_comparison(_mapping(value["average_wake_to_first"])),
        streak_at_end=_comparison(_mapping(value["streak_at_end"])),
    )


def _daily_chart_point(value: Mapping[str, Any]) -> DailyChartPoint:
    return DailyChartPoint(
        local_date=str(value["local_date"]),
        is_partial=bool(value["is_partial"]),
        session_count=int(value["session_count"]),
        violation_count=int(value["violation_count"]),
        average_earliness=_duration(value.get("average_earliness")),
        wake_to_first=_wake(_mapping(value["wake_to_first"])),
    )


def _weekly_chart_point(value: Mapping[str, Any]) -> WeeklyChartPoint:
    return WeeklyChartPoint(
        start_local_date=str(value["start_local_date"]),
        end_local_date_exclusive=str(value["end_local_date_exclusive"]),
        is_partial=bool(value["is_partial"]),
        total_sessions=int(value["total_sessions"]),
        total_violations=int(value["total_violations"]),
        average_earliness=_duration(value.get("average_earliness")),
        average_wake_to_first=_duration(value.get("average_wake_to_first")),
    )
