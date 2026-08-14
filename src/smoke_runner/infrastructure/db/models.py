"""SQLAlchemy table mappings for the SQLite persistence model."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="role"),
        CheckConstraint("status IN ('active', 'revoked')", name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    telegram_private_chat_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    timezone_name: Mapped[str] = mapped_column(
        String, nullable=False, server_default="Europe/Moscow"
    )
    milestone_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="milestone_notifications_enabled"),
        nullable=False,
        server_default=text("1"),
    )
    ai_commentary_enabled: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="ai_commentary_enabled"),
        nullable=False,
        server_default=text("0"),
    )
    last_feedback_template_key: Mapped[str | None] = mapped_column(String)
    activated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked_at_utc: Mapped[int | None] = mapped_column(Integer)
    created_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)


class InviteCodeRow(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    created_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    redeemed_at_utc: Mapped[int | None] = mapped_column(Integer)
    redeemed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    revoked_at_utc: Mapped[int | None] = mapped_column(Integer)


class SmokingSessionRow(Base):
    __tablename__ = "smoking_sessions"
    __table_args__ = (
        CheckConstraint("source IN ('now', 'backfill')", name="source"),
        Index(
            "ix_smoking_sessions_user_occurred_active",
            "user_id",
            "occurred_at_utc",
            "id",
            sqlite_where=text("deleted_at_utc IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    occurred_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_from_update_id: Mapped[int | None] = mapped_column(Integer)
    created_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    deleted_at_utc: Mapped[int | None] = mapped_column(Integer)


class WakeEventRow(Base):
    __tablename__ = "wake_events"
    __table_args__ = (
        CheckConstraint("source IN ('now', 'backfill')", name="source"),
        Index(
            "ix_wake_events_user_occurred_active",
            "user_id",
            "occurred_at_utc",
            "id",
            sqlite_where=text("deleted_at_utc IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    occurred_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_from_update_id: Mapped[int | None] = mapped_column(Integer)
    created_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    deleted_at_utc: Mapped[int | None] = mapped_column(Integer)


class IntervalChangeRow(Base):
    __tablename__ = "interval_changes"
    __table_args__ = (
        CheckConstraint("display_unit IN ('hour', 'day')", name="display_unit"),
        CheckConstraint(
            "((interval_seconds BETWEEN 3600 AND 86400 AND interval_seconds % 3600 = 0) "
            "OR (interval_seconds BETWEEN 86400 AND 604800 "
            "AND interval_seconds % 86400 = 0))",
            name="valid_interval",
        ),
        Index(
            "ix_interval_changes_user_effective",
            "user_id",
            "effective_at_utc",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    effective_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    display_unit: Mapped[str] = mapped_column(String, nullable=False)
    created_from_update_id: Mapped[int | None] = mapped_column(Integer)
    created_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)


class ProcessedUpdateRow(Base):
    __tablename__ = "processed_updates"
    __table_args__ = (
        CheckConstraint("outcome IN ('processed', 'ignored', 'rejected')", name="outcome"),
    )

    telegram_update_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    processed_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)


class MilestoneNotificationRow(Base):
    __tablename__ = "milestone_notifications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'claimed', 'sent', 'superseded', "
            "'skipped_stale', 'failed_unknown')",
            name="status",
        ),
        Index("ix_milestone_notifications_status_target", "status", "target_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    basis_session_id: Mapped[int] = mapped_column(
        ForeignKey("smoking_sessions.id"), nullable=False, index=True
    )
    target_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    claimed_at_utc: Mapped[int | None] = mapped_column(Integer)
    sent_at_utc: Mapped[int | None] = mapped_column(Integer)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String)
    created_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)


class ReportDeliveryRow(Base):
    __tablename__ = "report_deliveries"
    __table_args__ = (
        CheckConstraint("report_type IN ('daily', 'weekly')", name="report_type"),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'sent', 'failed', 'failed_unknown')",
            name="status",
        ),
        CheckConstraint("period_start_utc < period_end_utc", name="valid_period"),
        UniqueConstraint(
            "user_id",
            "report_type",
            "period_start_utc",
            "period_end_utc",
            name="uq_report_deliveries_user_period",
        ),
        Index("ix_report_deliveries_status_period_end", "status", "period_end_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String, nullable=False)
    period_start_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    is_partial: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="is_partial"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    claimed_at_utc: Mapped[int | None] = mapped_column(Integer)
    generated_at_utc: Mapped[int | None] = mapped_column(Integer)
    snapshot_json: Mapped[str | None] = mapped_column(Text)
    sent_at_utc: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String)
    created_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)


class ReportDeliveryPartRow(Base):
    __tablename__ = "report_delivery_parts"
    __table_args__ = (
        CheckConstraint(
            "part_type IN ('text', 'current_week_chart', 'history_chart')",
            name="part_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'sent', 'failed_unknown')",
            name="status",
        ),
        UniqueConstraint(
            "report_delivery_id",
            "ordinal",
            name="uq_report_delivery_parts_delivery_ordinal",
        ),
        Index("ix_report_delivery_parts_delivery_ordinal", "report_delivery_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_delivery_id: Mapped[int] = mapped_column(
        ForeignKey("report_deliveries.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    part_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    claimed_at_utc: Mapped[int | None] = mapped_column(Integer)
    sent_at_utc: Mapped[int | None] = mapped_column(Integer)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String)


class DashboardStateRow(Base):
    __tablename__ = "dashboard_state"
    __table_args__ = (
        CheckConstraint(
            "screen_kind IN ('dashboard', 'history', 'record', 'settings', 'report')",
            name="screen_kind",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    telegram_chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    screen_kind: Mapped[str] = mapped_column(String, nullable=False)
    active_until_utc: Mapped[int | None] = mapped_column(Integer)
    next_refresh_at_utc: Mapped[int | None] = mapped_column(Integer)
    updated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)


class RuntimeStateRow(Base):
    __tablename__ = "runtime_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at_utc: Mapped[int] = mapped_column(Integer, nullable=False)
