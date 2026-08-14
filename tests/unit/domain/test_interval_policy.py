"""Acceptance and property tests for the agreed interval policy."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.interval_policy import (
    ReactionClass,
    classify_session,
    significant_lateness_for,
)
from smoke_runner.domain.models import IntervalUnit, TargetInterval


def at(hour: int, minute: int = 0, second: int = 0) -> UtcInstant:
    return UtcInstant(datetime(2026, 8, 14, hour, minute, second, tzinfo=UTC))


@pytest.mark.parametrize(
    ("occurred_at", "reaction", "next_target_at"),
    [
        (at(11, 50), ReactionClass.EARLY, at(12, 50)),
        (at(12), ReactionClass.ON_SCHEDULE, at(13)),
        (at(12, 5), ReactionClass.ON_SCHEDULE, at(13)),
        (at(12, 14, 59), ReactionClass.ON_SCHEDULE, at(13)),
        (at(12, 15), ReactionClass.SIGNIFICANTLY_LATE, at(13, 15)),
        (at(12, 40), ReactionClass.SIGNIFICANTLY_LATE, at(13, 40)),
    ],
)
def test_one_hour_acceptance_examples(
    occurred_at: UtcInstant,
    reaction: ReactionClass,
    next_target_at: UtcInstant,
) -> None:
    timing = classify_session(
        occurred_at=occurred_at,
        target_at=at(12),
        interval=TargetInterval.hours(1),
    )

    assert timing.reaction is reaction
    assert timing.next_target_at == next_target_at


def test_significant_lateness_is_maximum_of_fifteen_minutes_and_quarter_interval() -> None:
    assert significant_lateness_for(TargetInterval.hours(1)) == timedelta(minutes=15)
    assert significant_lateness_for(TargetInterval.hours(6)) == timedelta(minutes=90)
    assert significant_lateness_for(TargetInterval.days(2)) == timedelta(hours=12)


@pytest.mark.parametrize(
    ("factory", "count"),
    [
        (TargetInterval.hours, 0),
        (TargetInterval.hours, 25),
        (TargetInterval.days, 0),
        (TargetInterval.days, 8),
    ],
)
def test_interval_rejects_out_of_range_values(factory: object, count: int) -> None:
    with pytest.raises(ValueError):
        factory(count)  # type: ignore[operator]


def test_interval_requires_an_integer_and_known_unit() -> None:
    with pytest.raises(TypeError):
        TargetInterval.hours(True)
    with pytest.raises(TypeError):
        TargetInterval(count=1, unit="hour")  # type: ignore[arg-type]


def test_twenty_four_hours_and_one_day_are_calculation_equivalents() -> None:
    assert TargetInterval.hours(24).seconds == TargetInterval.days(1).seconds
    assert TargetInterval.hours(24).unit is IntervalUnit.HOUR
    assert TargetInterval.days(1).unit is IntervalUnit.DAY


@given(
    interval_hours=st.integers(min_value=1, max_value=24),
    offset_seconds=st.integers(min_value=-7 * 24 * 60 * 60, max_value=7 * 24 * 60 * 60),
)
def test_next_target_is_always_after_the_basis_session(
    interval_hours: int, offset_seconds: int
) -> None:
    target = UtcInstant.from_unix_seconds(2_000_000_000)
    occurred = UtcInstant.from_unix_seconds(target.to_unix_seconds() + offset_seconds)

    timing = classify_session(
        occurred_at=occurred,
        target_at=target,
        interval=TargetInterval.hours(interval_hours),
    )

    assert timing.next_target_at > occurred


@given(offset_seconds=st.integers(min_value=-100_000, max_value=100_000))
def test_earliness_and_lateness_are_never_positive_together(offset_seconds: int) -> None:
    target = UtcInstant.from_unix_seconds(2_000_000_000)
    occurred = UtcInstant.from_unix_seconds(target.to_unix_seconds() + offset_seconds)

    timing = classify_session(
        occurred_at=occurred,
        target_at=target,
        interval=TargetInterval.hours(1),
    )

    assert not (timing.earliness > timedelta(0) and timing.lateness > timedelta(0))
