"""Outbound application ports."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from smoke_runner.application.models import UserContext
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import IntervalChange, SmokingSession, TargetInterval, WakeEvent
from smoke_runner.domain.report_models import ReportCommentaryInput
from smoke_runner.domain.timeline import Timeline


class ReportCommentator(Protocol):
    """Optional provider of a short comment after deterministic report generation."""

    async def comment(self, summary: ReportCommentaryInput) -> str | None:
        """Return an optional comment without modifying numeric report facts."""
        ...


class UserRepository(Protocol):
    async def get_context(self, user_id: int) -> UserContext | None: ...

    async def set_milestone_notifications(self, user_id: int, enabled: bool) -> None: ...


class ProcessedUpdateRepository(Protocol):
    async def claim(self, update_id: int, user_id: int, processed_at: UtcInstant) -> bool: ...


class SmokingSessionRepository(Protocol):
    async def list_active(self, user_id: int) -> tuple[SmokingSession, ...]: ...

    async def add(
        self,
        *,
        user_id: int,
        occurred_at: UtcInstant,
        source: str,
        update_id: int,
        now: UtcInstant,
    ) -> SmokingSession: ...

    async def edit(
        self,
        *,
        user_id: int,
        record_id: int,
        occurred_at: UtcInstant,
        now: UtcInstant,
    ) -> SmokingSession | None: ...

    async def soft_delete(self, *, user_id: int, record_id: int, now: UtcInstant) -> bool: ...


class WakeEventRepository(Protocol):
    async def list_active(self, user_id: int) -> tuple[WakeEvent, ...]: ...

    async def add(
        self,
        *,
        user_id: int,
        occurred_at: UtcInstant,
        source: str,
        update_id: int,
        now: UtcInstant,
    ) -> WakeEvent: ...

    async def edit(
        self,
        *,
        user_id: int,
        record_id: int,
        occurred_at: UtcInstant,
        now: UtcInstant,
    ) -> WakeEvent | None: ...

    async def soft_delete(self, *, user_id: int, record_id: int, now: UtcInstant) -> bool: ...


class IntervalChangeRepository(Protocol):
    async def list_all(self, user_id: int) -> tuple[IntervalChange, ...]: ...

    async def add(
        self,
        *,
        user_id: int,
        effective_at: UtcInstant,
        interval: TargetInterval,
        update_id: int,
        now: UtcInstant,
    ) -> IntervalChange: ...


class MilestoneRepository(Protocol):
    async def replace_pending(
        self,
        *,
        user_id: int,
        timeline: Timeline,
        now: UtcInstant,
        enabled: bool,
    ) -> None: ...


class TrackingUnitOfWork(Protocol):
    users: UserRepository
    processed_updates: ProcessedUpdateRepository
    sessions: SmokingSessionRepository
    wakes: WakeEventRepository
    intervals: IntervalChangeRepository
    milestones: MilestoneRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class TrackingUnitOfWorkFactory(Protocol):
    def __call__(self) -> TrackingUnitOfWork: ...
