"""Async SQLite engine and session factory configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

type SessionFactory = async_sessionmaker[AsyncSession]


def sqlite_url(database_path: Path) -> str:
    """Return an absolute aiosqlite URL for a database file."""
    return f"sqlite+aiosqlite:///{database_path.resolve()}"


def create_database_engine(database_path: Path, *, echo: bool = False) -> AsyncEngine:
    """Create the one-slot runtime engine required by the architecture."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(
        sqlite_url(database_path),
        echo=echo,
        pool_size=1,
        max_overflow=0,
        connect_args={"timeout": 5},
    )
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create sessions that retain loaded values after commit."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def _set_sqlite_pragmas(
    dbapi_connection: Any,
    connection_record: Any,
) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.execute("PRAGMA synchronous=FULL")
    finally:
        cursor.close()
