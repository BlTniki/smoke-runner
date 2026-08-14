"""UTC time value objects and injectable clocks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, Self, overload


@dataclass(frozen=True, order=True, slots=True)
class UtcInstant:
    """An absolute UTC instant with the same one-second precision as persistence."""

    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("UTC instant must be timezone-aware")
        normalized = self.value.astimezone(UTC)
        if normalized.microsecond != 0:
            raise ValueError("UTC instant must have whole-second precision")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def from_unix_seconds(cls, value: int) -> Self:
        """Create an instant from an integer Unix timestamp."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("Unix timestamp must be an integer number of seconds")
        return cls(datetime.fromtimestamp(value, tz=UTC))

    def to_unix_seconds(self) -> int:
        """Return the persistence representation for this instant."""
        return int(self.value.timestamp())

    def __add__(self, duration: timedelta) -> UtcInstant:
        return UtcInstant(self.value + _whole_seconds(duration))

    @overload
    def __sub__(self, other: UtcInstant) -> timedelta: ...

    @overload
    def __sub__(self, other: timedelta) -> UtcInstant: ...

    def __sub__(self, other: UtcInstant | timedelta) -> timedelta | UtcInstant:
        if isinstance(other, UtcInstant):
            return self.value - other.value
        if isinstance(other, timedelta):
            return UtcInstant(self.value - _whole_seconds(other))
        raise TypeError(f"Cannot subtract {type(other).__name__} from UtcInstant")


def _whole_seconds(duration: timedelta) -> timedelta:
    if duration.microseconds != 0:
        raise ValueError("Duration must have whole-second precision")
    return duration


class Clock(Protocol):
    """Source of current time supplied at application boundaries."""

    def now(self) -> UtcInstant:
        """Return the current UTC instant."""
        ...


class SystemClock:
    """Production clock using the operating system wall clock."""

    def now(self) -> UtcInstant:
        """Return current time rounded down to persistence precision."""
        current = datetime.now(UTC).replace(microsecond=0)
        return UtcInstant(current)
