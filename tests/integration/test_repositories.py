"""Integration tests for async sessions, invite redemption, and isolation."""

import asyncio

from sqlalchemy import func, select

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.infrastructure.db.engine import create_session_factory
from smoke_runner.infrastructure.db.models import (
    IntervalChangeRow,
    InviteCodeRow,
    SmokingSessionRow,
    UserRow,
)
from smoke_runner.infrastructure.db.repositories import (
    SqlAlchemyInviteRedemption,
    SqlAlchemySmokingSessionRepository,
)

NOW = UtcInstant.from_unix_seconds(2_000_000_000)


async def seed_admin_and_invite(session_factory) -> None:
    async with session_factory() as session, session.begin():
        admin = UserRow(
            telegram_user_id=1,
            telegram_private_chat_id=1,
            role="admin",
            status="active",
            timezone_name="UTC",
            activated_at_utc=NOW.to_unix_seconds(),
            created_at_utc=NOW.to_unix_seconds(),
            updated_at_utc=NOW.to_unix_seconds(),
        )
        session.add(admin)
        await session.flush()
        session.add(
            InviteCodeRow(
                code_digest="a" * 64,
                created_by_user_id=admin.id,
                created_at_utc=NOW.to_unix_seconds(),
                expires_at_utc=NOW.to_unix_seconds() + 3600,
            )
        )


async def test_each_concurrent_task_gets_a_distinct_async_session(db_engine) -> None:
    session_factory = create_session_factory(db_engine)

    async def session_identity() -> int:
        async with session_factory() as session:
            await asyncio.sleep(0)
            return id(session)

    identities = await asyncio.gather(session_identity(), session_identity())

    assert len(set(identities)) == 2


async def test_concurrent_invite_redemption_creates_only_one_user(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    await seed_admin_and_invite(session_factory)

    async def redeem(telegram_id: int):
        async with session_factory() as session, session.begin():
            return await SqlAlchemyInviteRedemption(session).redeem(
                code_digest="a" * 64,
                telegram_user_id=telegram_id,
                telegram_private_chat_id=telegram_id,
                timezone_name="UTC",
                now=NOW,
            )

    results = await asyncio.gather(redeem(10), redeem(20))

    assert sum(result is not None for result in results) == 1
    async with session_factory() as session:
        member_count = await session.scalar(
            select(func.count()).select_from(UserRow).where(UserRow.role == "member")
        )
        interval_count = await session.scalar(select(func.count()).select_from(IntervalChangeRow))
        invite = await session.scalar(
            select(InviteCodeRow).where(InviteCodeRow.code_digest == "a" * 64)
        )
    assert member_count == 1
    assert interval_count == 1
    assert invite is not None
    assert invite.redeemed_at_utc == NOW.to_unix_seconds()
    assert invite.redeemed_by_user_id is not None


async def test_timeline_repository_is_scoped_by_user(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session, session.begin():
        users = [
            UserRow(
                telegram_user_id=value,
                telegram_private_chat_id=value,
                role="member",
                status="active",
                timezone_name="UTC",
                activated_at_utc=NOW.to_unix_seconds(),
                created_at_utc=NOW.to_unix_seconds(),
                updated_at_utc=NOW.to_unix_seconds(),
            )
            for value in (10, 20)
        ]
        session.add_all(users)
        await session.flush()
        session.add_all(
            [
                SmokingSessionRow(
                    user_id=user.id,
                    occurred_at_utc=NOW.to_unix_seconds() + user.id,
                    source="now",
                    created_at_utc=NOW.to_unix_seconds(),
                    updated_at_utc=NOW.to_unix_seconds(),
                )
                for user in users
            ]
        )

    async with session_factory() as session:
        first_user_sessions = await SqlAlchemySmokingSessionRepository(session).list_active(
            users[0].id
        )

    assert len(first_user_sessions) == 1
    assert (
        first_user_sessions[0].occurred_at.to_unix_seconds() == NOW.to_unix_seconds() + users[0].id
    )
