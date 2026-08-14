"""Shared migrated SQLite fixtures."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from smoke_runner.infrastructure.db.engine import create_database_engine
from smoke_runner.infrastructure.db.migrations import upgrade_database

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated_database(tmp_path: Path) -> Path:
    database_path = tmp_path / "smoke-runner.sqlite3"
    upgrade_database(database_path, config_path=PROJECT_ROOT / "alembic.ini")
    return database_path


@pytest.fixture
async def db_engine(migrated_database: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_database_engine(migrated_database)
    try:
        yield engine
    finally:
        await engine.dispose()
