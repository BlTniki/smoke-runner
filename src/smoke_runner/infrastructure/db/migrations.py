"""Programmatic Alembic migration entry point."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from smoke_runner.infrastructure.db.engine import sqlite_url


def upgrade_database(
    database_path: Path,
    *,
    config_path: Path = Path("alembic.ini"),
) -> None:
    """Upgrade one SQLite database to the current schema revision."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(config_path.resolve()))
    config.set_main_option("sqlalchemy.url", sqlite_url(database_path))
    command.upgrade(config, "head")
