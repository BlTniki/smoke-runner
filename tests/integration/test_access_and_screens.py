"""Stage-three security, isolation and restart-persistence acceptance tests."""

from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from smoke_runner.application.security import AdminBootstrapService, InviteService
from smoke_runner.domain.clock import UtcInstant
from smoke_runner.infrastructure.db.engine import create_session_factory
from smoke_runner.infrastructure.db.gateway import DatabaseGateway, HistoryKind
from smoke_runner.infrastructure.db.models import (
    AdminBootstrapCodeRow,
    InviteCodeRow,
    SmokingSessionRow,
    UserRow,
)
from smoke_runner.infrastructure.telegram.keyboards import dashboard_keyboard
from smoke_runner.infrastructure.telegram.screens import ScreenManager

NOW = UtcInstant.from_unix_seconds(2_000_000_000)


@dataclass
class MutableClock:
    value: UtcInstant = NOW

    def now(self) -> UtcInstant:
        return self.value


class FakeBot:
    def __init__(self) -> None:
        self.sent = 0
        self.edited: list[int] = []
        self.deleted: list[int] = []

    async def send_message(self, **kwargs):
        del kwargs
        self.sent += 1
        return SimpleNamespace(message_id=700 + self.sent)

    async def edit_message_text(self, **kwargs):
        self.edited.append(int(kwargs["message_id"]))

    async def delete_message(self, **kwargs):
        self.deleted.append(int(kwargs["message_id"]))


async def _create_member(gateway: DatabaseGateway, clock: MutableClock, telegram_id: int):
    admin = await gateway.bootstrap_admin(
        telegram_user_id=1,
        timezone_name="UTC",
        now=clock.now(),
    )
    invites = InviteService(
        gateway,
        pepper="p" * 32,
        clock=clock,
        ttl=timedelta(days=7),
        timezone_name="UTC",
    )
    code = await invites.create(admin)
    member = await invites.redeem(
        plaintext_code=code,
        telegram_user_id=telegram_id,
        telegram_private_chat_id=telegram_id,
    )
    assert member is not None
    return admin, member, code


async def test_invite_is_hashed_one_time_and_revocation_blocks_auth(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    gateway = DatabaseGateway(session_factory)
    clock = MutableClock()

    admin, member, plaintext = await _create_member(gateway, clock, 10)

    async with session_factory() as session:
        invite = await session.scalar(select(InviteCodeRow))
        assert invite is not None
        assert invite.code_digest != plaintext
        assert len(invite.code_digest) == 64
    service = InviteService(
        gateway,
        pepper="p" * 32,
        clock=clock,
        ttl=timedelta(days=7),
        timezone_name="UTC",
    )
    assert (
        await service.redeem(
            plaintext_code=plaintext,
            telegram_user_id=20,
            telegram_private_chat_id=20,
        )
        is None
    )
    assert await gateway.revoke_user(
        admin_user_id=admin.id,
        target_user_id=member.id,
        now=clock.now(),
    )
    assert await gateway.find_active_user(10) is None
    assert not await gateway.revoke_user(
        admin_user_id=admin.id,
        target_user_id=admin.id,
        now=clock.now(),
    )


async def test_expired_invite_is_rejected(db_engine) -> None:
    gateway = DatabaseGateway(create_session_factory(db_engine))
    clock = MutableClock()
    admin = await gateway.bootstrap_admin(telegram_user_id=1, timezone_name="UTC", now=NOW)
    service = InviteService(
        gateway,
        pepper="p" * 32,
        clock=clock,
        ttl=timedelta(hours=1),
        timezone_name="UTC",
    )
    code = await service.create(admin)
    clock.value = NOW + timedelta(hours=1)

    assert (
        await service.redeem(
            plaintext_code=code,
            telegram_user_id=10,
            telegram_private_chat_id=10,
        )
        is None
    )


async def test_history_record_lookup_cannot_cross_user_boundary(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    gateway = DatabaseGateway(session_factory)
    clock = MutableClock()
    _, first, _ = await _create_member(gateway, clock, 10)
    _, second, _ = await _create_member(gateway, clock, 20)
    async with session_factory() as session, session.begin():
        row = SmokingSessionRow(
            user_id=first.id,
            occurred_at_utc=NOW.to_unix_seconds(),
            source="now",
            created_at_utc=NOW.to_unix_seconds(),
            updated_at_utc=NOW.to_unix_seconds(),
        )
        session.add(row)
        await session.flush()
        record_id = row.id

    assert (
        await gateway.get_history_item(
            user_id=second.id,
            kind=HistoryKind.SESSION,
            record_id=record_id,
        )
        is None
    )


async def test_new_screen_manager_replaces_persisted_message_and_deletes_old_screen(
    db_engine,
) -> None:
    gateway = DatabaseGateway(create_session_factory(db_engine))
    clock = MutableClock()
    admin = await gateway.bootstrap_admin(telegram_user_id=1, timezone_name="UTC", now=NOW)
    bot = FakeBot()

    first_manager = ScreenManager(gateway, clock)
    first_id = await first_manager.show(
        bot=bot,  # type: ignore[arg-type]
        user=admin,
        chat_id=1,
        text="first",
        reply_markup=dashboard_keyboard(),
        screen_kind="dashboard",
    )
    restarted_manager = ScreenManager(gateway, clock)
    second_id = await restarted_manager.show(
        bot=bot,  # type: ignore[arg-type]
        user=admin,
        chat_id=1,
        text="second",
        reply_markup=dashboard_keyboard(),
        screen_kind="history",
    )

    assert first_id == 701
    assert second_id == 702
    assert bot.sent == 2
    assert bot.edited == []
    assert bot.deleted == [701]
    state = await gateway.get_dashboard_state(admin.id)
    assert state is not None
    assert state.screen_kind == "history"
    assert state.telegram_message_id == 702


async def test_admin_bootstrap_rotates_code_and_binds_exactly_one_admin(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    gateway = DatabaseGateway(session_factory)
    clock = MutableClock()
    service = AdminBootstrapService(
        gateway,
        pepper="p" * 32,
        clock=clock,
        ttl=timedelta(minutes=30),
        timezone_name="UTC",
    )

    first = await service.issue()
    second = await service.issue()
    assert first is not None
    assert second is not None
    assert first.plaintext != second.plaintext
    async with session_factory() as session:
        stored_codes = (await session.scalars(select(AdminBootstrapCodeRow))).all()
    assert len(stored_codes) == 1
    assert stored_codes[0].code_digest == service.digest(second.plaintext)
    assert stored_codes[0].code_digest != second.plaintext

    assert (
        await service.redeem(
            plaintext_code=first.plaintext,
            telegram_user_id=10,
            telegram_private_chat_id=10,
        )
        is None
    )
    admin = await service.redeem(
        plaintext_code=second.plaintext,
        telegram_user_id=20,
        telegram_private_chat_id=20,
    )
    assert admin is not None
    assert admin.is_admin
    assert await service.issue() is None
    async with session_factory() as session:
        admin_rows = (await session.scalars(select(UserRow).where(UserRow.role == "admin"))).all()
        stored_codes = (await session.scalars(select(AdminBootstrapCodeRow))).all()
    assert len(admin_rows) == 1
    assert stored_codes == []


async def test_concurrent_admin_bootstrap_claim_creates_one_admin(db_engine) -> None:
    import asyncio

    session_factory = create_session_factory(db_engine)
    gateway = DatabaseGateway(session_factory)
    service = AdminBootstrapService(
        gateway,
        pepper="p" * 32,
        clock=MutableClock(),
        ttl=timedelta(minutes=30),
        timezone_name="UTC",
    )
    issued = await service.issue()
    assert issued is not None

    results = await asyncio.gather(
        service.redeem(
            plaintext_code=issued.plaintext,
            telegram_user_id=10,
            telegram_private_chat_id=10,
        ),
        service.redeem(
            plaintext_code=issued.plaintext,
            telegram_user_id=20,
            telegram_private_chat_id=20,
        ),
    )

    assert sum(result is not None for result in results) == 1


async def test_expired_admin_bootstrap_code_is_rejected(db_engine) -> None:
    gateway = DatabaseGateway(create_session_factory(db_engine))
    clock = MutableClock()
    service = AdminBootstrapService(
        gateway,
        pepper="p" * 32,
        clock=clock,
        ttl=timedelta(minutes=30),
        timezone_name="UTC",
    )
    issued = await service.issue()
    assert issued is not None
    clock.value = issued.expires_at

    assert (
        await service.redeem(
            plaintext_code=issued.plaintext,
            telegram_user_id=10,
            telegram_private_chat_id=10,
        )
        is None
    )


async def test_configured_admin_cannot_replace_bound_administrator(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    gateway = DatabaseGateway(session_factory)
    first = await gateway.bootstrap_admin(
        telegram_user_id=10,
        timezone_name="UTC",
        now=NOW,
    )

    with pytest.raises(RuntimeError, match="different Telegram administrator"):
        await gateway.bootstrap_admin(
            telegram_user_id=20,
            timezone_name="UTC",
            now=NOW,
        )

    assert (await gateway.find_active_user(10)) == first
    assert await gateway.find_active_user(20) is None
