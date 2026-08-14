"""Domain facts used to rebuild a user's smoking timeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from smoke_runner.domain.clock import UtcInstant


class IntervalUnit(StrEnum):
    """Unit selected by the user when configuring an interval."""

    HOUR = "hour"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class TargetInterval:
    """Validated target interval while retaining the user's display unit."""

    count: int
    unit: IntervalUnit

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise TypeError("Interval count must be an integer")
        if not isinstance(self.unit, IntervalUnit):
            raise TypeError("Interval unit must be IntervalUnit.HOUR or IntervalUnit.DAY")

        limits = {
            IntervalUnit.HOUR: (1, 24),
            IntervalUnit.DAY: (1, 7),
        }
        minimum, maximum = limits[self.unit]
        if not minimum <= self.count <= maximum:
            raise ValueError(
                f"Interval in {self.unit.value}s must be between {minimum} and {maximum}"
            )

    @property
    def seconds(self) -> int:
        """Return the normalized calculation value."""
        seconds_per_unit = {
            IntervalUnit.HOUR: 60 * 60,
            IntervalUnit.DAY: 24 * 60 * 60,
        }
        return self.count * seconds_per_unit[self.unit]

    @classmethod
    def hours(cls, count: int) -> TargetInterval:
        return cls(count=count, unit=IntervalUnit.HOUR)

    @classmethod
    def days(cls, count: int) -> TargetInterval:
        return cls(count=count, unit=IntervalUnit.DAY)


def _validate_record_id(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Record id must be a positive integer")


@dataclass(frozen=True, slots=True)
class SmokingSession:
    """A confirmed smoking event, sorted by occurrence time and stable id."""

    id: int
    occurred_at: UtcInstant

    def __post_init__(self) -> None:
        _validate_record_id(self.id)


@dataclass(frozen=True, slots=True)
class WakeEvent:
    """A confirmed primary wake-up event."""

    id: int
    occurred_at: UtcInstant

    def __post_init__(self) -> None:
        _validate_record_id(self.id)


@dataclass(frozen=True, slots=True)
class IntervalChange:
    """A target interval becoming effective at a specific UTC instant."""

    id: int
    effective_at: UtcInstant
    interval: TargetInterval

    def __post_init__(self) -> None:
        _validate_record_id(self.id)
