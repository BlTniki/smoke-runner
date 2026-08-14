"""Deterministic reconstruction of a user's target timeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.interval_policy import (
    ZERO_DURATION,
    ReactionClass,
    classify_session,
    significant_lateness_for,
)
from smoke_runner.domain.models import IntervalChange, SmokingSession, TargetInterval


class TimelineError(ValueError):
    """Base error for invalid timeline input."""


class DuplicateRecordIdError(TimelineError):
    """Raised when a source contains the same stable id more than once."""


class MissingIntervalError(TimelineError):
    """Raised when a session occurs before any interval setting exists."""


@dataclass(frozen=True, slots=True)
class SessionAssessment:
    """Calculated facts for one session in chronological context."""

    session: SmokingSession
    interval: TargetInterval
    target_at: UtcInstant | None
    earliness: timedelta
    lateness: timedelta
    significant_lateness: timedelta
    reaction: ReactionClass
    next_target_at: UtcInstant

    @property
    def is_violation(self) -> bool:
        """Return whether the session occurred before its target."""
        return self.earliness > ZERO_DURATION


@dataclass(frozen=True, slots=True)
class Timeline:
    """A fully reconstructed history and its current target state."""

    sessions: tuple[SessionAssessment, ...]
    active_interval: TargetInterval | None
    current_target_at: UtcInstant | None


type TimelineEvent = SmokingSession | IntervalChange


def build_timeline(
    sessions: tuple[SmokingSession, ...] | list[SmokingSession],
    interval_changes: tuple[IntervalChange, ...] | list[IntervalChange],
) -> Timeline:
    """Recompute the complete timeline from source facts.

    Interval changes at the same timestamp are applied before smoking sessions.
    Rebuilding from facts is also the edit/delete/backfill invalidation strategy.
    """
    _ensure_unique_ids(sessions, record_type="smoking session")
    _ensure_unique_ids(interval_changes, record_type="interval change")

    events: list[TimelineEvent] = [*sessions, *interval_changes]
    events.sort(key=_event_sort_key)

    active_interval: TargetInterval | None = None
    current_target: UtcInstant | None = None
    last_session: SmokingSession | None = None
    assessments: list[SessionAssessment] = []

    for event in events:
        if isinstance(event, IntervalChange):
            active_interval = event.interval
            if last_session is not None:
                current_target = last_session.occurred_at + timedelta(
                    seconds=active_interval.seconds
                )
            continue

        if active_interval is None:
            raise MissingIntervalError(
                f"Smoking session {event.id} occurs before the first interval change"
            )

        if current_target is None:
            current_target = event.occurred_at + timedelta(seconds=active_interval.seconds)
            assessment = SessionAssessment(
                session=event,
                interval=active_interval,
                target_at=None,
                earliness=ZERO_DURATION,
                lateness=ZERO_DURATION,
                significant_lateness=significant_lateness_for(active_interval),
                reaction=ReactionClass.FIRST_SESSION,
                next_target_at=current_target,
            )
        else:
            timing = classify_session(
                occurred_at=event.occurred_at,
                target_at=current_target,
                interval=active_interval,
            )
            current_target = timing.next_target_at
            assessment = SessionAssessment(
                session=event,
                interval=active_interval,
                target_at=timing.target_at,
                earliness=timing.earliness,
                lateness=timing.lateness,
                significant_lateness=timing.significant_lateness,
                reaction=timing.reaction,
                next_target_at=timing.next_target_at,
            )

        assessments.append(assessment)
        last_session = event

    return Timeline(
        sessions=tuple(assessments),
        active_interval=active_interval,
        current_target_at=current_target,
    )


def _event_sort_key(event: TimelineEvent) -> tuple[UtcInstant, int, int]:
    event_kind = 0 if isinstance(event, IntervalChange) else 1
    event_at = event.effective_at if isinstance(event, IntervalChange) else event.occurred_at
    return event_at, event_kind, event.id


def _ensure_unique_ids(
    records: tuple[SmokingSession, ...]
    | list[SmokingSession]
    | tuple[IntervalChange, ...]
    | list[IntervalChange],
    *,
    record_type: str,
) -> None:
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        raise DuplicateRecordIdError(f"Duplicate {record_type} id")
