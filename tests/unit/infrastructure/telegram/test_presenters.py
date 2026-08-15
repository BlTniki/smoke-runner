"""Dashboard and history presenter acceptance examples."""

from smoke_runner.application.models import UserContext
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import IntervalChange, SmokingSession, TargetInterval, WakeEvent
from smoke_runner.infrastructure.db.gateway import DashboardFacts
from smoke_runner.infrastructure.telegram.presenters import render_dashboard


def instant(value: int) -> UtcInstant:
    return UtcInstant.from_unix_seconds(value)


def test_dashboard_exposes_target_morning_progress_and_streak() -> None:
    now = instant(1_735_723_800)  # 2025-01-01 11:30 UTC
    facts = DashboardFacts(
        user=UserContext(
            id=1,
            timezone_name="UTC",
            activated_at=instant(1_735_689_600),
            milestone_notifications_enabled=True,
        ),
        sessions=(SmokingSession(id=1, occurred_at=instant(1_735_722_000)),),
        wakes=(WakeEvent(id=1, occurred_at=instant(1_735_718_400)),),
        intervals=(
            IntervalChange(
                id=1,
                effective_at=instant(1_735_689_600),
                interval=TargetInterval.hours(1),
            ),
        ),
        last_feedback_template_key=None,
    )

    text = render_dashboard(facts, now)

    assert "Интервал: 1 ч" in text
    assert "Следующий рубеж: 01.01.2025 10:00" in text
    assert "Осталось: 30 мин" in text
    assert "От пробуждения: 1 ч" in text
    assert "Серия:" in text
