"""Transaction-scoped SQLAlchemy Unit of Work."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession

from smoke_runner.infrastructure.db.engine import SessionFactory
from smoke_runner.infrastructure.db.repositories import (
    SqlAlchemyIntervalChangeRepository,
    SqlAlchemyMilestoneRepository,
    SqlAlchemyProcessedUpdateRepository,
    SqlAlchemySmokingSessionRepository,
    SqlAlchemyUserRepository,
    SqlAlchemyWakeEventRepository,
)


class SqlAlchemyUnitOfWork:
    """Create one AsyncSession and one transaction per use-case invocation."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        session = self._session_factory()
        self.session = session
        await session.begin()
        self.users = SqlAlchemyUserRepository(session)
        self.processed_updates = SqlAlchemyProcessedUpdateRepository(session)
        self.sessions = SqlAlchemySmokingSessionRepository(session)
        self.wakes = SqlAlchemyWakeEventRepository(session)
        self.intervals = SqlAlchemyIntervalChangeRepository(session)
        self.milestones = SqlAlchemyMilestoneRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if self.session is None:
            return
        try:
            if exc_type is None:
                try:
                    await self.session.commit()
                except BaseException:
                    await self.session.rollback()
                    raise
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
