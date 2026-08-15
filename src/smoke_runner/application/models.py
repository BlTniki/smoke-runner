"""Infrastructure-neutral command and result models for tracking use cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import TargetInterval


class EventSource(StrEnum):
    NOW = "now"
    BACKFILL = "backfill"


@dataclass(frozen=True, slots=True)
class UserContext:
    id: int
    timezone_name: str
    activated_at: UtcInstant
    milestone_notifications_enabled: bool


@dataclass(frozen=True, slots=True)
class LogEventCommand:
    user_id: int
    telegram_update_id: int
    occurred_at: UtcInstant
    source: EventSource


@dataclass(frozen=True, slots=True)
class EditEventCommand:
    user_id: int
    telegram_update_id: int
    record_id: int
    occurred_at: UtcInstant


@dataclass(frozen=True, slots=True)
class DeleteEventCommand:
    user_id: int
    telegram_update_id: int
    record_id: int


@dataclass(frozen=True, slots=True)
class ChangeIntervalCommand:
    user_id: int
    telegram_update_id: int
    interval: TargetInterval


@dataclass(frozen=True, slots=True)
class SetMilestoneNotificationsCommand:
    user_id: int
    telegram_update_id: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class MutationResult:
    applied: bool
    record_id: int | None
