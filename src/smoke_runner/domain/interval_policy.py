"""Rules for classifying a session and selecting the next target."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import TargetInterval

ZERO_DURATION = timedelta(0)
MINIMUM_SIGNIFICANT_LATENESS = timedelta(minutes=15)


class ReactionClass(StrEnum):
    """Fact class used later by supportive message templates."""

    FIRST_SESSION = "first_session"
    EARLY = "early"
    ON_SCHEDULE = "on_schedule"
    SIGNIFICANTLY_LATE = "significantly_late"


@dataclass(frozen=True, slots=True)
class SessionTiming:
    """Calculated facts for a session that has a previous target."""

    target_at: UtcInstant
    earliness: timedelta
    lateness: timedelta
    significant_lateness: timedelta
    reaction: ReactionClass
    next_target_at: UtcInstant


def significant_lateness_for(interval: TargetInterval) -> timedelta:
    """Return max(15 minutes, 25% of the target interval)."""
    quarter_interval = timedelta(seconds=interval.seconds // 4)
    return max(MINIMUM_SIGNIFICANT_LATENESS, quarter_interval)


def classify_session(
    *, occurred_at: UtcInstant, target_at: UtcInstant, interval: TargetInterval
) -> SessionTiming:
    """Apply the agreed piecewise target rule to one classifiable session."""
    interval_duration = timedelta(seconds=interval.seconds)
    threshold = significant_lateness_for(interval)

    if occurred_at < target_at:
        earliness = target_at - occurred_at
        return SessionTiming(
            target_at=target_at,
            earliness=earliness,
            lateness=ZERO_DURATION,
            significant_lateness=threshold,
            reaction=ReactionClass.EARLY,
            next_target_at=occurred_at + interval_duration,
        )

    lateness = occurred_at - target_at
    if lateness < threshold:
        return SessionTiming(
            target_at=target_at,
            earliness=ZERO_DURATION,
            lateness=lateness,
            significant_lateness=threshold,
            reaction=ReactionClass.ON_SCHEDULE,
            next_target_at=target_at + interval_duration,
        )

    return SessionTiming(
        target_at=target_at,
        earliness=ZERO_DURATION,
        lateness=lateness,
        significant_lateness=threshold,
        reaction=ReactionClass.SIGNIFICANTLY_LATE,
        next_target_at=occurred_at + interval_duration,
    )
