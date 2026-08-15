"""Invite-code generation and authentication-facing application models."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from smoke_runner.domain.clock import Clock, UtcInstant


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: int
    telegram_user_id: int
    telegram_private_chat_id: int
    role: str
    timezone_name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True, slots=True)
class ManagedUser:
    id: int
    telegram_user_id: int
    role: str
    status: str


class AccessGateway(Protocol):
    async def find_active_user(self, telegram_user_id: int) -> AuthenticatedUser | None: ...

    async def insert_invite(
        self,
        *,
        created_by_user_id: int,
        code_digest: str,
        created_at: UtcInstant,
        expires_at: UtcInstant,
    ) -> None: ...

    async def redeem_invite(
        self,
        *,
        code_digest: str,
        telegram_user_id: int,
        telegram_private_chat_id: int,
        timezone_name: str,
        now: UtcInstant,
    ) -> AuthenticatedUser | None: ...


class InviteService:
    """Create and redeem opaque one-use invite codes.

    Only an HMAC-SHA256 digest is persisted. The returned plaintext has 192 bits
    of randomness and can safely be carried as a Telegram ``/start`` payload.
    """

    def __init__(
        self,
        gateway: AccessGateway,
        *,
        pepper: str,
        clock: Clock,
        ttl: timedelta,
        timezone_name: str,
    ) -> None:
        if len(pepper.encode()) < 32:
            raise ValueError("Invite pepper must contain at least 32 bytes")
        if ttl <= timedelta(0):
            raise ValueError("Invite TTL must be positive")
        self._gateway = gateway
        self._pepper = pepper.encode()
        self._clock = clock
        self._ttl = ttl
        self._timezone_name = timezone_name

    def digest(self, plaintext_code: str) -> str:
        return hmac.new(self._pepper, plaintext_code.encode(), hashlib.sha256).hexdigest()

    async def create(self, admin: AuthenticatedUser) -> str:
        if not admin.is_admin:
            raise PermissionError("Only an administrator can create invites")
        now = self._clock.now()
        plaintext = secrets.token_urlsafe(24)
        await self._gateway.insert_invite(
            created_by_user_id=admin.id,
            code_digest=self.digest(plaintext),
            created_at=now,
            expires_at=now + self._ttl,
        )
        return plaintext

    async def redeem(
        self,
        *,
        plaintext_code: str,
        telegram_user_id: int,
        telegram_private_chat_id: int,
    ) -> AuthenticatedUser | None:
        code = plaintext_code.strip()
        if not code:
            return None
        return await self._gateway.redeem_invite(
            code_digest=self.digest(code),
            telegram_user_id=telegram_user_id,
            telegram_private_chat_id=telegram_private_chat_id,
            timezone_name=self._timezone_name,
            now=self._clock.now(),
        )
