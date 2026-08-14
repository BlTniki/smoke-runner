"""Idempotent transactional use cases for tracking facts."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from smoke_runner.application.models import (
    ChangeIntervalCommand,
    DeleteEventCommand,
    EditEventCommand,
    LogEventCommand,
    MutationResult,
    UserContext,
)
from smoke_runner.application.ports import TrackingUnitOfWork, TrackingUnitOfWorkFactory
from smoke_runner.domain.clock import Clock, UtcInstant
from smoke_runner.domain.local_time import local_date_of
from smoke_runner.domain.timeline import build_timeline


class TrackingError(ValueError):
    """Base error for rejected tracking mutations."""


class UnknownUserError(TrackingError):
    pass


class RecordNotFoundError(TrackingError):
    pass


class EventOutsideTrackedPeriodError(TrackingError):
    pass


class WakeAlreadyExistsError(TrackingError):
    pass


class TrackingService:
    """Application service whose every public method is one DB transaction."""

    def __init__(self, uow_factory: TrackingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def log_session(self, command: LogEventCommand) -> MutationResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await self._claim_and_get_user(uow, command, now)
            if user is None:
                return MutationResult(applied=False, record_id=None)
            self._validate_event_time(command.occurred_at, user, now)
            session = await uow.sessions.add(
                user_id=user.id,
                occurred_at=command.occurred_at,
                source=command.source.value,
                update_id=command.telegram_update_id,
                now=now,
            )
            await self._rebuild_milestone(uow, user, now)
            return MutationResult(applied=True, record_id=session.id)

    async def edit_session(self, command: EditEventCommand) -> MutationResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await self._claim_and_get_user(uow, command, now)
            if user is None:
                return MutationResult(applied=False, record_id=None)
            self._validate_event_time(command.occurred_at, user, now)
            edited = await uow.sessions.edit(
                user_id=user.id,
                record_id=command.record_id,
                occurred_at=command.occurred_at,
                now=now,
            )
            if edited is None:
                raise RecordNotFoundError("Smoking session not found")
            await self._rebuild_milestone(uow, user, now)
            return MutationResult(applied=True, record_id=edited.id)

    async def delete_session(self, command: DeleteEventCommand) -> MutationResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await self._claim_and_get_user(uow, command, now)
            if user is None:
                return MutationResult(applied=False, record_id=None)
            deleted = await uow.sessions.soft_delete(
                user_id=user.id,
                record_id=command.record_id,
                now=now,
            )
            if not deleted:
                raise RecordNotFoundError("Smoking session not found")
            await self._rebuild_milestone(uow, user, now)
            return MutationResult(applied=True, record_id=command.record_id)

    async def log_wake(
        self,
        command: LogEventCommand,
        *,
        replace_existing: bool = False,
    ) -> MutationResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await self._claim_and_get_user(uow, command, now)
            if user is None:
                return MutationResult(applied=False, record_id=None)
            self._validate_event_time(command.occurred_at, user, now)
            timezone = ZoneInfo(user.timezone_name)
            event_date = local_date_of(command.occurred_at, timezone)
            same_day = tuple(
                wake
                for wake in await uow.wakes.list_active(user.id)
                if local_date_of(wake.occurred_at, timezone) == event_date
            )
            if same_day and not replace_existing:
                raise WakeAlreadyExistsError("A primary wake already exists for this date")
            for wake in same_day:
                await uow.wakes.soft_delete(user_id=user.id, record_id=wake.id, now=now)
            wake = await uow.wakes.add(
                user_id=user.id,
                occurred_at=command.occurred_at,
                source=command.source.value,
                update_id=command.telegram_update_id,
                now=now,
            )
            return MutationResult(applied=True, record_id=wake.id)

    async def edit_wake(self, command: EditEventCommand) -> MutationResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await self._claim_and_get_user(uow, command, now)
            if user is None:
                return MutationResult(applied=False, record_id=None)
            self._validate_event_time(command.occurred_at, user, now)
            timezone = ZoneInfo(user.timezone_name)
            event_date = local_date_of(command.occurred_at, timezone)
            collision = any(
                wake.id != command.record_id
                and local_date_of(wake.occurred_at, timezone) == event_date
                for wake in await uow.wakes.list_active(user.id)
            )
            if collision:
                raise WakeAlreadyExistsError("A primary wake already exists for this date")
            edited = await uow.wakes.edit(
                user_id=user.id,
                record_id=command.record_id,
                occurred_at=command.occurred_at,
                now=now,
            )
            if edited is None:
                raise RecordNotFoundError("Wake event not found")
            return MutationResult(applied=True, record_id=edited.id)

    async def delete_wake(self, command: DeleteEventCommand) -> MutationResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await self._claim_and_get_user(uow, command, now)
            if user is None:
                return MutationResult(applied=False, record_id=None)
            deleted = await uow.wakes.soft_delete(
                user_id=user.id,
                record_id=command.record_id,
                now=now,
            )
            if not deleted:
                raise RecordNotFoundError("Wake event not found")
            return MutationResult(applied=True, record_id=command.record_id)

    async def change_interval(self, command: ChangeIntervalCommand) -> MutationResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await self._claim_and_get_user(uow, command, now)
            if user is None:
                return MutationResult(applied=False, record_id=None)
            interval = await uow.intervals.add(
                user_id=user.id,
                effective_at=now,
                interval=command.interval,
                update_id=command.telegram_update_id,
                now=now,
            )
            await self._rebuild_milestone(uow, user, now)
            return MutationResult(applied=True, record_id=interval.id)

    async def _claim_and_get_user(
        self,
        uow: TrackingUnitOfWork,
        command: LogEventCommand | EditEventCommand | DeleteEventCommand | ChangeIntervalCommand,
        now: UtcInstant,
    ) -> UserContext | None:
        claimed = await uow.processed_updates.claim(
            command.telegram_update_id,
            command.user_id,
            now,
        )
        if not claimed:
            return None
        user = await uow.users.get_context(command.user_id)
        if user is None:
            raise UnknownUserError("Active user not found")
        return user

    @staticmethod
    def _validate_event_time(
        occurred_at: UtcInstant,
        user: UserContext,
        now: UtcInstant,
    ) -> None:
        if occurred_at < user.activated_at:
            raise EventOutsideTrackedPeriodError("Event cannot be before user activation")
        if occurred_at > now:
            raise EventOutsideTrackedPeriodError("Event cannot be in the future")

    @staticmethod
    async def _rebuild_milestone(
        uow: TrackingUnitOfWork,
        user: UserContext,
        now: UtcInstant,
    ) -> None:
        timeline = build_timeline(
            list(await uow.sessions.list_active(user.id)),
            list(await uow.intervals.list_all(user.id)),
        )
        await uow.milestones.replace_pending(
            user_id=user.id,
            timeline=timeline,
            now=now,
            enabled=user.milestone_notifications_enabled,
        )
