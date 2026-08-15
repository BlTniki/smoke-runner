"""Invite cryptography and authorization application tests."""

from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from smoke_runner.application.security import AuthenticatedUser, InviteService
from smoke_runner.domain.clock import UtcInstant

NOW = UtcInstant.from_unix_seconds(2_000_000_000)


@dataclass
class FixedClock:
    value: UtcInstant = NOW

    def now(self) -> UtcInstant:
        return self.value


@dataclass
class FakeGateway:
    inserted: list[dict[str, object]] = field(default_factory=list)

    async def find_active_user(self, telegram_user_id: int) -> AuthenticatedUser | None:
        del telegram_user_id
        return None

    async def insert_invite(self, **values: object) -> None:
        self.inserted.append(values)

    async def redeem_invite(self, **values: object) -> AuthenticatedUser | None:
        del values
        return None


async def test_created_invite_has_192_bits_and_only_digest_reaches_gateway() -> None:
    gateway = FakeGateway()
    service = InviteService(
        gateway,
        pepper="p" * 32,
        clock=FixedClock(),
        ttl=timedelta(days=7),
        timezone_name="UTC",
    )
    admin = AuthenticatedUser(
        id=1,
        telegram_user_id=100,
        telegram_private_chat_id=100,
        role="admin",
        timezone_name="UTC",
    )

    plaintext = await service.create(admin)

    assert len(plaintext) == 32
    persisted = gateway.inserted[0]
    assert plaintext not in persisted.values()
    assert persisted["code_digest"] == service.digest(plaintext)
    assert len(str(persisted["code_digest"])) == 64


async def test_member_cannot_create_invite() -> None:
    service = InviteService(
        FakeGateway(),
        pepper="p" * 32,
        clock=FixedClock(),
        ttl=timedelta(days=7),
        timezone_name="UTC",
    )
    member = AuthenticatedUser(1, 100, 100, "member", "UTC")

    with pytest.raises(PermissionError):
        await service.create(member)
