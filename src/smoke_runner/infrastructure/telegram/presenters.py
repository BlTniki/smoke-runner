"""Pure Russian-language Telegram text presenters."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.feedback import FeedbackFacts, FeedbackTemplate, render_feedback
from smoke_runner.domain.local_time import local_date_of
from smoke_runner.domain.models import IntervalUnit, TargetInterval
from smoke_runner.domain.streaks import TodayStreakStatus, calculate_dashboard_streaks
from smoke_runner.domain.timeline import SessionAssessment, build_timeline
from smoke_runner.infrastructure.db.gateway import DashboardFacts, HistoryItem, HistoryKind


def render_dashboard(facts: DashboardFacts, now: UtcInstant) -> str:
    timezone = ZoneInfo(facts.user.timezone_name)
    timeline = build_timeline(list(facts.sessions), list(facts.intervals))
    streaks = calculate_dashboard_streaks(
        timeline,
        activated_at=facts.user.activated_at,
        now=now,
        timezone=timezone,
    )
    interval = timeline.active_interval
    lines = ["Твой режим", ""]
    lines.append(f"Интервал: {format_interval(interval) if interval else 'ещё не задан'}")
    if timeline.sessions:
        last = timeline.sessions[-1].session.occurred_at
        lines.append(f"Последняя сессия: {format_local(last, timezone)}")
    else:
        lines.append("Последняя сессия: пока нет")

    target = timeline.current_target_at
    if target is None:
        lines.append("Следующий рубеж появится после первой сессии.")
    elif target > now:
        lines.append(f"Следующий рубеж: {format_local(target, timezone)}")
        lines.append(f"Осталось: {format_duration(target - now)}")
    else:
        lines.append(f"Рубеж достигнут: {format_local(target, timezone)}")
        lines.append(f"Уже можно {format_duration(now - target)}")

    today = local_date_of(now, timezone)
    wake = next(
        (
            wake
            for wake in reversed(facts.wakes)
            if local_date_of(wake.occurred_at, timezone) == today
        ),
        None,
    )
    if wake is None:
        lines.append("Утро: пробуждение не отмечено")
    else:
        first_after_wake = next(
            (
                assessment.session.occurred_at
                for assessment in timeline.sessions
                if assessment.session.occurred_at >= wake.occurred_at
                and local_date_of(assessment.session.occurred_at, timezone) == today
            ),
            None,
        )
        morning_end = first_after_wake or now
        suffix = "до первой сессии" if first_after_wake else "и счёт идёт"
        lines.append(
            f"От пробуждения: {format_duration(morning_end - wake.occurred_at)} — {suffix}"
        )

    today_text = (
        "сегодня пока без нарушений"
        if streaks.today is TodayStreakStatus.NO_VIOLATION_YET
        else "сегодня было нарушение — мягко продолжаем"
    )
    lines.append(f"Серия: {streaks.completed.current} завершённых дн. без нарушений; {today_text}.")
    lines.append("")
    lines.append("Данные обновлены: " + format_local(now, timezone, include_date=False))
    return "\n".join(lines)


def render_history(items: tuple[HistoryItem, ...], timezone: ZoneInfo, offset: int) -> str:
    lines = ["История", ""]
    if not items:
        lines.append("Записей здесь пока нет.")
    for index, item in enumerate(items, start=offset + 1):
        icon = "💨" if item.kind is HistoryKind.SESSION else "☀️"
        source = "сейчас" if item.source == "now" else "задним числом"
        lines.append(f"{index}. {icon} {format_local(item.occurred_at, timezone)} · {source}")
    return "\n".join(lines)


def render_record(item: HistoryItem, timezone: ZoneInfo) -> str:
    kind = "Сессия" if item.kind is HistoryKind.SESSION else "Пробуждение"
    source = "кнопка «сейчас»" if item.source == "now" else "добавлено задним числом"
    return "\n".join(
        (
            kind,
            "",
            f"Фактическое время: {format_local(item.occurred_at, timezone)}",
            f"Способ: {source}",
            f"Запись создана: {format_local(item.created_at, timezone)}",
        )
    )


def render_session_feedback(
    template: FeedbackTemplate,
    assessment: SessionAssessment,
    timezone: ZoneInfo,
) -> str:
    return render_feedback(
        template,
        FeedbackFacts.from_assessment(assessment),
        format_instant=lambda instant: format_local(instant, timezone, include_date=False),
        format_duration=format_duration,
    )


def format_interval(interval: TargetInterval) -> str:
    suffix = "ч" if interval.unit is IntervalUnit.HOUR else "дн"
    return f"{interval.count} {suffix}"


def format_local(
    instant: UtcInstant,
    timezone: ZoneInfo,
    *,
    include_date: bool = True,
) -> str:
    local = instant.value.astimezone(timezone)
    return local.strftime("%d.%m.%Y %H:%M" if include_date else "%H:%M")


def format_duration(value: timedelta) -> str:
    total = max(0, int(value.total_seconds()))
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, _seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} дн")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append("меньше 1 мин")
    return " ".join(parts)
