"""admin bootstrap code and single-admin invariant

Revision ID: 20260815_0002
Revises: 20260814_0001
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_0002"
down_revision: str | None = "20260814_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_bootstrap_codes",
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at_utc", sa.Integer(), nullable=False),
        sa.Column("expires_at_utc", sa.Integer(), nullable=False),
        sa.CheckConstraint("slot = 1", name=op.f("ck_admin_bootstrap_codes_singleton_slot")),
        sa.PrimaryKeyConstraint("slot", name=op.f("pk_admin_bootstrap_codes")),
        sa.UniqueConstraint(
            "code_digest",
            name=op.f("uq_admin_bootstrap_codes_code_digest"),
        ),
    )
    op.create_index(
        "uq_users_single_admin",
        "users",
        ["role"],
        unique=True,
        sqlite_where=sa.text("role = 'admin'"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_single_admin", table_name="users")
    op.drop_table("admin_bootstrap_codes")
