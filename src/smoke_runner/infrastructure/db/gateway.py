"""Transactional access, query and Telegram screen persistence gateway."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from smoke_runner.application.models import UserContext
from smoke_runner.application.security import AuthenticatedUser, ManagedUser
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.models import IntervalChange, SmokingSession, WakeEvent
from smoke_runner.infrastructure.db.engine import SessionFactory
from smoke_runner.infrastructure.db.models import (
    AdminBootstrapCodeRow,
    DashboardStateRow,
    IntervalChangeRow,
    InviteCodeRow,
    SmokingSessionRow,
    UserRow,
    WakeEventRow,
)
from smoke_runner.infrastructure.db.repositories import (
    SqlAlchemyInviteRedemption,
    _to_interval_change,
)


@dataclass(frozen=True, slots=True)
class DashboardFacts:
    user: UserContext
    sessions: tuple[SmokingSession, ...]
    wakes: tuple[WakeEvent, ...]
    intervals: tuple[IntervalChange, ...]
    last_feedback_template_key: str | None
    ai_commentary_enabled: bool = False


class HistoryKind(StrEnum):
    SESSION = "session"
    WAKE = "wake"


@dataclass(frozen=True, slots=True)
class HistoryItem:
    kind: HistoryKind
    id: int
    occurred_at: UtcInstant
    source: str
    created_at: UtcInstant


@dataclass(frozen=True, slots=True)
class DashboardState:
    user_id: int
    telegram_chat_id: int
    telegram_message_id: int | None
    screen_kind: str
    active_until: UtcInstant | None
    next_refresh_at: UtcInstant | None


class DatabaseGateway:
    """Small transaction-per-call gateway for auth and read-side bot flows."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def bootstrap_admin(
        self,
        *,
        telegram_user_id: int,
        timezone_name: str,
        now: UtcInstant,
    ) -> AuthenticatedUser:
        now_seconds = now.to_unix_seconds()
        async with self._session_factory() as session, session.begin():
            current_admin = await session.scalar(select(UserRow).where(UserRow.role == "admin"))
            if current_admin is not None and current_admin.telegram_user_id != telegram_user_id:
                raise RuntimeError(
                    "The database is already bound to a different Telegram administrator"
                )
            row = await session.scalar(
                select(UserRow).where(UserRow.telegram_user_id == telegram_user_id)
            )
            if row is None:
                row = UserRow(
                    telegram_user_id=telegram_user_id,
                    telegram_private_chat_id=telegram_user_id,
                    role="admin",
                    status="active",
                    timezone_name=timezone_name,
                    milestone_notifications_enabled=True,
                    ai_commentary_enabled=False,
                    activated_at_utc=now_seconds,
                    created_at_utc=now_seconds,
                    updated_at_utc=now_seconds,
                )
                session.add(row)
                await session.flush()
            else:
                row.role = "admin"
                row.status = "active"
                row.revoked_at_utc = None
                row.updated_at_utc = now_seconds

            has_interval = await session.scalar(
                select(IntervalChangeRow.id).where(IntervalChangeRow.user_id == row.id).limit(1)
            )
            if has_interval is None:
                session.add(
                    IntervalChangeRow(
                        user_id=row.id,
                        effective_at_utc=row.activated_at_utc,
                        interval_seconds=3600,
                        display_unit="hour",
                        created_at_utc=now_seconds,
                    )
                )
            await session.execute(delete(AdminBootstrapCodeRow))
            await session.flush()
            return _authenticated(row)

    async def issue_admin_bootstrap_code(
        self,
        *,
        code_digest: str,
        created_at: UtcInstant,
        expires_at: UtcInstant,
    ) -> bool:
        """Replace the singleton code only while no administrator exists."""
        async with self._session_factory() as session, session.begin():
            admin_id = await session.scalar(
                select(UserRow.id).where(UserRow.role == "admin").limit(1)
            )
            if admin_id is not None:
                await session.execute(delete(AdminBootstrapCodeRow))
                return False
            statement = (
                sqlite_insert(AdminBootstrapCodeRow)
                .values(
                    slot=1,
                    code_digest=code_digest,
                    created_at_utc=created_at.to_unix_seconds(),
                    expires_at_utc=expires_at.to_unix_seconds(),
                )
                .on_conflict_do_update(
                    index_elements=[AdminBootstrapCodeRow.slot],
                    set_={
                        "code_digest": code_digest,
                        "created_at_utc": created_at.to_unix_seconds(),
                        "expires_at_utc": expires_at.to_unix_seconds(),
                    },
                )
            )
            await session.execute(statement)
            return True

    async def redeem_admin_bootstrap_code(
        self,
        *,
        code_digest: str,
        telegram_user_id: int,
        telegram_private_chat_id: int,
        timezone_name: str,
        now: UtcInstant,
    ) -> AuthenticatedUser | None:
        """Claim the singleton code and create the only administrator atomically."""
        now_seconds = now.to_unix_seconds()
        try:
            async with self._session_factory() as session, session.begin():
                admin_id = await session.scalar(
                    select(UserRow.id).where(UserRow.role == "admin").limit(1)
                )
                if admin_id is not None:
                    await session.execute(delete(AdminBootstrapCodeRow))
                    return None
                claimed = await session.scalar(
                    delete(AdminBootstrapCodeRow)
                    .where(
                        AdminBootstrapCodeRow.slot == 1,
                        AdminBootstrapCodeRow.code_digest == code_digest,
                        AdminBootstrapCodeRow.expires_at_utc > now_seconds,
                    )
                    .returning(AdminBootstrapCodeRow.slot)
                )
                if claimed is None:
                    return None

                user = await session.scalar(
                    select(UserRow).where(UserRow.telegram_user_id == telegram_user_id)
                )
                if user is None:
                    user = UserRow(
                        telegram_user_id=telegram_user_id,
                        telegram_private_chat_id=telegram_private_chat_id,
                        role="admin",
                        status="active",
                        timezone_name=timezone_name,
                        milestone_notifications_enabled=True,
                        ai_commentary_enabled=False,
                        activated_at_utc=now_seconds,
                        created_at_utc=now_seconds,
                        updated_at_utc=now_seconds,
                    )
                    session.add(user)
                    await session.flush()
                else:
                    user.telegram_private_chat_id = telegram_private_chat_id
                    user.role = "admin"
                    user.status = "active"
                    user.revoked_at_utc = None
                    user.updated_at_utc = now_seconds

                has_interval = await session.scalar(
                    select(IntervalChangeRow.id)
                    .where(IntervalChangeRow.user_id == user.id)
                    .limit(1)
                )
                if has_interval is None:
                    session.add(
                        IntervalChangeRow(
                            user_id=user.id,
                            effective_at_utc=user.activated_at_utc,
                            interval_seconds=3600,
                            display_unit="hour",
                            created_at_utc=now_seconds,
                        )
                    )
                await session.flush()
                return _authenticated(user)
        except IntegrityError:
            # A concurrent claim or the unique single-admin index won the race.
            return None

    async def find_active_user(self, telegram_user_id: int) -> AuthenticatedUser | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(UserRow).where(
                    UserRow.telegram_user_id == telegram_user_id,
                    UserRow.status == "active",
                )
            )
            return None if row is None else _authenticated(row)

    async def insert_invite(
        self,
        *,
        created_by_user_id: int,
        code_digest: str,
        created_at: UtcInstant,
        expires_at: UtcInstant,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            creator = await session.scalar(
                select(UserRow).where(
                    UserRow.id == created_by_user_id,
                    UserRow.role == "admin",
                    UserRow.status == "active",
                )
            )
            if creator is None:
                raise PermissionError("Active administrator not found")
            session.add(
                InviteCodeRow(
                    code_digest=code_digest,
                    created_by_user_id=created_by_user_id,
                    created_at_utc=created_at.to_unix_seconds(),
                    expires_at_utc=expires_at.to_unix_seconds(),
                )
            )

    async def redeem_invite(
        self,
        *,
        code_digest: str,
        telegram_user_id: int,
        telegram_private_chat_id: int,
        timezone_name: str,
        now: UtcInstant,
    ) -> AuthenticatedUser | None:
        try:
            async with self._session_factory() as session, session.begin():
                already = await session.scalar(
                    select(UserRow).where(UserRow.telegram_user_id == telegram_user_id)
                )
                if already is not None:
                    return _authenticated(already) if already.status == "active" else None
                redeemed = await SqlAlchemyInviteRedemption(session).redeem(
                    code_digest=code_digest,
                    telegram_user_id=telegram_user_id,
                    telegram_private_chat_id=telegram_private_chat_id,
                    timezone_name=timezone_name,
                    now=now,
                )
                if redeemed is None:
                    return None
                row = await session.get(UserRow, redeemed.id)
                assert row is not None
                return _authenticated(row)
        except IntegrityError:
            # Concurrent redemption or a Telegram identity collision is a clean rejection.
            return None

    async def list_users(self, admin_user_id: int) -> tuple[ManagedUser, ...]:
        async with self._session_factory() as session:
            await _require_admin(session, admin_user_id)
            rows = (
                await session.scalars(select(UserRow).order_by(UserRow.created_at_utc, UserRow.id))
            ).all()
            return tuple(
                ManagedUser(
                    id=row.id,
                    telegram_user_id=row.telegram_user_id,
                    role=row.role,
                    status=row.status,
                )
                for row in rows
            )

    async def revoke_user(
        self,
        *,
        admin_user_id: int,
        target_user_id: int,
        now: UtcInstant,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            await _require_admin(session, admin_user_id)
            result = await session.execute(
                update(UserRow)
                .where(
                    UserRow.id == target_user_id,
                    UserRow.role != "admin",
                    UserRow.status == "active",
                )
                .values(
                    status="revoked",
                    revoked_at_utc=now.to_unix_seconds(),
                    updated_at_utc=now.to_unix_seconds(),
                )
                .returning(UserRow.id)
            )
            return result.scalar_one_or_none() is not None

    async def dashboard_facts(self, user_id: int) -> DashboardFacts | None:
        async with self._session_factory() as session:
            user = await session.scalar(
                select(UserRow).where(UserRow.id == user_id, UserRow.status == "active")
            )
            if user is None:
                return None
            session_rows = (
                await session.scalars(
                    select(SmokingSessionRow)
                    .where(
                        SmokingSessionRow.user_id == user_id,
                        SmokingSessionRow.deleted_at_utc.is_(None),
                    )
                    .order_by(SmokingSessionRow.occurred_at_utc, SmokingSessionRow.id)
                )
            ).all()
            wake_rows = (
                await session.scalars(
                    select(WakeEventRow)
                    .where(
                        WakeEventRow.user_id == user_id,
                        WakeEventRow.deleted_at_utc.is_(None),
                    )
                    .order_by(WakeEventRow.occurred_at_utc, WakeEventRow.id)
                )
            ).all()
            interval_rows = (
                await session.scalars(
                    select(IntervalChangeRow)
                    .where(IntervalChangeRow.user_id == user_id)
                    .order_by(IntervalChangeRow.effective_at_utc, IntervalChangeRow.id)
                )
            ).all()
            return DashboardFacts(
                user=UserContext(
                    id=user.id,
                    timezone_name=user.timezone_name,
                    activated_at=UtcInstant.from_unix_seconds(user.activated_at_utc),
                    milestone_notifications_enabled=user.milestone_notifications_enabled,
                ),
                sessions=tuple(
                    SmokingSession(
                        id=row.id,
                        occurred_at=UtcInstant.from_unix_seconds(row.occurred_at_utc),
                    )
                    for row in session_rows
                ),
                wakes=tuple(
                    WakeEvent(
                        id=row.id,
                        occurred_at=UtcInstant.from_unix_seconds(row.occurred_at_utc),
                    )
                    for row in wake_rows
                ),
                intervals=tuple(_to_interval_change(row) for row in interval_rows),
                last_feedback_template_key=user.last_feedback_template_key,
                ai_commentary_enabled=user.ai_commentary_enabled,
            )

    async def set_feedback_template_key(self, user_id: int, key: str) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(UserRow)
                .where(UserRow.id == user_id, UserRow.status == "active")
                .values(last_feedback_template_key=key)
            )

    async def history(
        self,
        *,
        user_id: int,
        offset: int,
        limit: int,
        start_at: UtcInstant | None = None,
        end_at: UtcInstant | None = None,
    ) -> tuple[HistoryItem, ...]:
        async with self._session_factory() as session:
            session_query = select(SmokingSessionRow).where(
                SmokingSessionRow.user_id == user_id,
                SmokingSessionRow.deleted_at_utc.is_(None),
            )
            wake_query = select(WakeEventRow).where(
                WakeEventRow.user_id == user_id,
                WakeEventRow.deleted_at_utc.is_(None),
            )
            if start_at is not None:
                session_query = session_query.where(
                    SmokingSessionRow.occurred_at_utc >= start_at.to_unix_seconds()
                )
                wake_query = wake_query.where(
                    WakeEventRow.occurred_at_utc >= start_at.to_unix_seconds()
                )
            if end_at is not None:
                session_query = session_query.where(
                    SmokingSessionRow.occurred_at_utc < end_at.to_unix_seconds()
                )
                wake_query = wake_query.where(
                    WakeEventRow.occurred_at_utc < end_at.to_unix_seconds()
                )
            session_rows = (await session.scalars(session_query)).all()
            wake_rows = (await session.scalars(wake_query)).all()
            items = [
                HistoryItem(
                    kind=HistoryKind.SESSION,
                    id=row.id,
                    occurred_at=UtcInstant.from_unix_seconds(row.occurred_at_utc),
                    source=row.source,
                    created_at=UtcInstant.from_unix_seconds(row.created_at_utc),
                )
                for row in session_rows
            ]
            items.extend(
                HistoryItem(
                    kind=HistoryKind.WAKE,
                    id=row.id,
                    occurred_at=UtcInstant.from_unix_seconds(row.occurred_at_utc),
                    source=row.source,
                    created_at=UtcInstant.from_unix_seconds(row.created_at_utc),
                )
                for row in wake_rows
            )
            items.sort(key=lambda item: (item.occurred_at, item.id), reverse=True)
            return tuple(items[offset : offset + limit])

    async def get_history_item(
        self, *, user_id: int, kind: HistoryKind, record_id: int
    ) -> HistoryItem | None:
        async with self._session_factory() as session:
            if kind is HistoryKind.SESSION:
                session_row = await session.scalar(
                    select(SmokingSessionRow).where(
                        SmokingSessionRow.id == record_id,
                        SmokingSessionRow.user_id == user_id,
                        SmokingSessionRow.deleted_at_utc.is_(None),
                    )
                )
                if session_row is None:
                    return None
                return HistoryItem(
                    kind=kind,
                    id=session_row.id,
                    occurred_at=UtcInstant.from_unix_seconds(session_row.occurred_at_utc),
                    source=session_row.source,
                    created_at=UtcInstant.from_unix_seconds(session_row.created_at_utc),
                )

            wake_row = await session.scalar(
                select(WakeEventRow).where(
                    WakeEventRow.id == record_id,
                    WakeEventRow.user_id == user_id,
                    WakeEventRow.deleted_at_utc.is_(None),
                )
            )
            if wake_row is None:
                return None
            return HistoryItem(
                kind=kind,
                id=wake_row.id,
                occurred_at=UtcInstant.from_unix_seconds(wake_row.occurred_at_utc),
                source=wake_row.source,
                created_at=UtcInstant.from_unix_seconds(wake_row.created_at_utc),
            )

    async def get_dashboard_state(self, user_id: int) -> DashboardState | None:
        async with self._session_factory() as session:
            row = await session.get(DashboardStateRow, user_id)
            if row is None:
                return None
            return DashboardState(
                user_id=row.user_id,
                telegram_chat_id=row.telegram_chat_id,
                telegram_message_id=row.telegram_message_id,
                screen_kind=row.screen_kind,
                active_until=(
                    None
                    if row.active_until_utc is None
                    else UtcInstant.from_unix_seconds(row.active_until_utc)
                ),
                next_refresh_at=(
                    None
                    if row.next_refresh_at_utc is None
                    else UtcInstant.from_unix_seconds(row.next_refresh_at_utc)
                ),
            )

    async def save_dashboard_state(
        self,
        *,
        user_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
        screen_kind: str,
        now: UtcInstant,
        active_until: UtcInstant | None,
        next_refresh_at: UtcInstant | None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(DashboardStateRow, user_id)
            if row is None:
                session.add(
                    DashboardStateRow(
                        user_id=user_id,
                        telegram_chat_id=telegram_chat_id,
                        telegram_message_id=telegram_message_id,
                        screen_kind=screen_kind,
                        active_until_utc=(
                            None if active_until is None else active_until.to_unix_seconds()
                        ),
                        next_refresh_at_utc=(
                            None if next_refresh_at is None else next_refresh_at.to_unix_seconds()
                        ),
                        updated_at_utc=now.to_unix_seconds(),
                    )
                )
            else:
                row.telegram_chat_id = telegram_chat_id
                row.telegram_message_id = telegram_message_id
                row.screen_kind = screen_kind
                row.active_until_utc = (
                    None if active_until is None else active_until.to_unix_seconds()
                )
                row.next_refresh_at_utc = (
                    None if next_refresh_at is None else next_refresh_at.to_unix_seconds()
                )
                row.updated_at_utc = now.to_unix_seconds()


def _authenticated(row: UserRow) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=row.id,
        telegram_user_id=row.telegram_user_id,
        telegram_private_chat_id=row.telegram_private_chat_id,
        role=row.role,
        timezone_name=row.timezone_name,
    )


async def _require_admin(session: AsyncSession, user_id: int) -> None:
    row = await session.scalar(
        select(UserRow.id).where(
            UserRow.id == user_id,
            UserRow.role == "admin",
            UserRow.status == "active",
        )
    )
    if row is None:
        raise PermissionError("Active administrator not found")
