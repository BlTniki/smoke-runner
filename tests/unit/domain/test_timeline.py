"""Tests for deterministic full-history timeline reconstruction."""

from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.interval_policy import ReactionClass
from smoke_runner.domain.models import IntervalChange, SmokingSession, TargetInterval
from smoke_runner.domain.timeline import (
    DuplicateRecordIdError,
    MissingIntervalError,
    build_timeline,
)

BASE = 2_000_000_000


def instant(offset_seconds: int) -> UtcInstant:
    return UtcInstant.from_unix_seconds(BASE + offset_seconds)


def change(
    *, record_id: int = 1, offset_seconds: int = -1, interval_hours: int = 1
) -> IntervalChange:
    return IntervalChange(
        id=record_id,
        effective_at=instant(offset_seconds),
        interval=TargetInterval.hours(interval_hours),
    )


def test_first_session_is_neutral_and_starts_a_full_interval() -> None:
    timeline = build_timeline(
        [SmokingSession(id=1, occurred_at=instant(0))],
        [change()],
    )

    assessment = timeline.sessions[0]
    assert assessment.target_at is None
    assert assessment.reaction is ReactionClass.FIRST_SESSION
    assert assessment.next_target_at == instant(60 * 60)
    assert not assessment.is_violation


def test_interval_change_at_same_timestamp_applies_before_session() -> None:
    timeline = build_timeline(
        [SmokingSession(id=1, occurred_at=instant(0))],
        [
            change(record_id=1, offset_seconds=-10, interval_hours=1),
            change(record_id=2, offset_seconds=0, interval_hours=2),
        ],
    )

    assert timeline.sessions[0].interval == TargetInterval.hours(2)
    assert timeline.current_target_at == instant(2 * 60 * 60)


def test_interval_change_resets_current_target_from_last_session() -> None:
    timeline = build_timeline(
        [SmokingSession(id=1, occurred_at=instant(0))],
        [
            change(record_id=1, offset_seconds=-1, interval_hours=1),
            change(record_id=2, offset_seconds=30 * 60, interval_hours=2),
        ],
    )

    assert timeline.sessions[0].next_target_at == instant(60 * 60)
    assert timeline.current_target_at == instant(2 * 60 * 60)
    assert timeline.active_interval == TargetInterval.hours(2)


def test_backfill_uses_interval_that_was_active_at_its_occurrence() -> None:
    timeline = build_timeline(
        [
            SmokingSession(id=2, occurred_at=instant(2 * 60 * 60)),
            SmokingSession(id=1, occurred_at=instant(0)),
        ],
        [
            change(record_id=1, offset_seconds=-1, interval_hours=1),
            change(record_id=2, offset_seconds=30 * 60, interval_hours=2),
        ],
    )

    first, second = timeline.sessions
    assert first.interval == TargetInterval.hours(1)
    assert second.interval == TargetInterval.hours(2)
    assert second.target_at == instant(2 * 60 * 60)
    assert second.reaction is ReactionClass.ON_SCHEDULE


def test_sessions_with_same_timestamp_use_stable_id_order() -> None:
    timeline = build_timeline(
        [
            SmokingSession(id=2, occurred_at=instant(0)),
            SmokingSession(id=1, occurred_at=instant(0)),
        ],
        [change()],
    )

    assert [assessment.session.id for assessment in timeline.sessions] == [1, 2]
    assert timeline.sessions[1].reaction is ReactionClass.EARLY


def test_session_before_first_interval_is_rejected() -> None:
    with pytest.raises(MissingIntervalError):
        build_timeline(
            [SmokingSession(id=1, occurred_at=instant(0))],
            [change(offset_seconds=1)],
        )


def test_duplicate_source_ids_are_rejected() -> None:
    with pytest.raises(DuplicateRecordIdError):
        build_timeline(
            [
                SmokingSession(id=1, occurred_at=instant(0)),
                SmokingSession(id=1, occurred_at=instant(1)),
            ],
            [change()],
        )


def test_editing_and_restoring_a_session_recomputes_following_targets() -> None:
    original_sessions = [
        SmokingSession(id=1, occurred_at=instant(0)),
        SmokingSession(id=2, occurred_at=instant(60 * 60 + 5 * 60)),
        SmokingSession(id=3, occurred_at=instant(2 * 60 * 60 + 5 * 60)),
    ]
    interval_changes = [change()]
    original = build_timeline(original_sessions, interval_changes)

    edited_sessions = [
        original_sessions[0],
        SmokingSession(id=2, occurred_at=instant(50 * 60)),
        original_sessions[2],
    ]
    edited = build_timeline(edited_sessions, interval_changes)
    restored = build_timeline(original_sessions, interval_changes)

    assert edited != original
    assert restored == original


@given(latenesses=st.lists(st.integers(min_value=0, max_value=14 * 60 + 59), max_size=20))
def test_small_latenesses_do_not_accumulate_target_drift(latenesses: list[int]) -> None:
    sessions = [SmokingSession(id=1, occurred_at=instant(0))]
    for index, lateness in enumerate(latenesses, start=1):
        grid_target = index * 60 * 60
        sessions.append(SmokingSession(id=index + 1, occurred_at=instant(grid_target + lateness)))

    timeline = build_timeline(sessions, [change()])

    for index, assessment in enumerate(timeline.sessions[1:], start=1):
        assert assessment.target_at == instant(index * 60 * 60)
        assert assessment.next_target_at == instant((index + 1) * 60 * 60)


@given(
    offsets=st.lists(
        st.integers(min_value=0, max_value=7 * 24 * 60 * 60),
        unique=True,
        max_size=20,
    ),
    backfill_offset=st.integers(min_value=0, max_value=7 * 24 * 60 * 60),
)
def test_inserting_then_deleting_backfill_restores_original_timeline(
    offsets: list[int], backfill_offset: int
) -> None:
    sessions = [
        SmokingSession(id=index, occurred_at=instant(offset))
        for index, offset in enumerate(offsets, start=1)
    ]
    interval_changes = [change()]
    original = build_timeline(sessions, interval_changes)

    with_backfill = [
        *sessions,
        SmokingSession(id=999, occurred_at=instant(backfill_offset)),
    ]
    build_timeline(with_backfill, interval_changes)
    restored = build_timeline(
        [session for session in with_backfill if session.id != 999],
        interval_changes,
    )

    assert restored == original
    assert all(
        assessment.next_target_at > assessment.session.occurred_at
        for assessment in restored.sessions
    )
    assert all(
        not (assessment.earliness > timedelta(0) and assessment.lateness > timedelta(0))
        for assessment in restored.sessions
    )
