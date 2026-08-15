"""Integration tests for the initial Alembic schema."""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from smoke_runner.infrastructure.db.engine import create_session_factory
from smoke_runner.infrastructure.db.migrations import upgrade_database
from smoke_runner.infrastructure.db.models import SmokingSessionRow, UserRow

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "admin_bootstrap_codes",
    "alembic_version",
    "dashboard_state",
    "interval_changes",
    "invite_codes",
    "milestone_notifications",
    "processed_updates",
    "report_deliveries",
    "report_delivery_parts",
    "runtime_state",
    "smoking_sessions",
    "users",
    "wake_events",
}


def test_upgrade_on_empty_database_and_repeated_upgrade_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"

    upgrade_database(database_path, config_path=PROJECT_ROOT / "alembic.ini")
    upgrade_database(database_path, config_path=PROJECT_ROOT / "alembic.ini")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert EXPECTED_TABLES <= tables
    assert revision == ("20260815_0002",)


def test_partial_timeline_indexes_are_present(migrated_database: Path) -> None:
    with sqlite3.connect(migrated_database) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            ("ix_smoking_sessions_user_occurred_active",),
        ).fetchone()

    assert sql is not None
    assert "WHERE deleted_at_utc IS NULL" in sql[0]


def test_timeline_query_uses_the_partial_index(migrated_database: Path) -> None:
    with sqlite3.connect(migrated_database) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT id, occurred_at_utc FROM smoking_sessions "
            "WHERE user_id = ? AND deleted_at_utc IS NULL "
            "ORDER BY occurred_at_utc, id",
            (1,),
        ).fetchall()

    assert any("ix_smoking_sessions_user_occurred_active" in row[3] for row in plan)


def test_initial_migration_can_downgrade_and_upgrade_again(tmp_path: Path) -> None:
    database_path = tmp_path / "downgrade.sqlite3"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{database_path}",
    )

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        user_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
    assert user_table == ("users",)


async def test_runtime_engine_applies_required_pragmas(db_engine) -> None:
    async with db_engine.connect() as connection:
        foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
        busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))
        journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
        synchronous = await connection.scalar(text("PRAGMA synchronous"))

    assert foreign_keys == 1
    assert busy_timeout == 5000
    assert journal_mode == "delete"
    assert synchronous == 2
    assert db_engine.pool.size() == 1


async def test_foreign_key_and_unique_constraints_are_enforced(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    now = 2_000_000_000
    async with session_factory() as session:
        session.add(
            SmokingSessionRow(
                user_id=999,
                occurred_at_utc=now,
                source="now",
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        session.add(
            UserRow(
                telegram_user_id=10,
                telegram_private_chat_id=10,
                role="member",
                status="active",
                timezone_name="UTC",
                activated_at_utc=now,
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        await session.commit()

        session.add(
            UserRow(
                telegram_user_id=10,
                telegram_private_chat_id=11,
                role="member",
                status="active",
                timezone_name="UTC",
                activated_at_utc=now,
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_database_rejects_a_second_administrator(db_engine) -> None:
    session_factory = create_session_factory(db_engine)
    now = 2_000_000_000
    async with session_factory() as session:
        session.add_all(
            [
                UserRow(
                    telegram_user_id=value,
                    telegram_private_chat_id=value,
                    role="admin",
                    status="active",
                    timezone_name="UTC",
                    activated_at_utc=now,
                    created_at_utc=now,
                    updated_at_utc=now,
                )
                for value in (10, 20)
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
