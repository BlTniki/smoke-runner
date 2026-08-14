"""Tests for the disabled MVP report commentator."""

from datetime import UTC, datetime

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import UtcPeriod
from smoke_runner.domain.metrics import (
    DailyMetrics,
    WakeToFirstMetric,
    WakeToFirstStatus,
)
from smoke_runner.domain.report_models import (
    build_daily_report_snapshot,
    commentary_input_from_snapshot,
)
from smoke_runner.infrastructure.ai_commentary import DisabledReportCommentator


async def test_disabled_commentator_returns_none() -> None:
    start = UtcInstant(datetime(2026, 8, 14, tzinfo=UTC))
    end = UtcInstant(datetime(2026, 8, 15, tzinfo=UTC))
    metrics = DailyMetrics(
        local_date=start.value.date(),
        period=UtcPeriod(start=start, end=end),
        is_partial=False,
        session_count=0,
        classifiable_session_count=0,
        violation_count=0,
        average_earliness=None,
        maximum_earliness=None,
        wake_to_first=WakeToFirstMetric(
            status=WakeToFirstStatus.MISSING_WAKE,
            wake=None,
            first_session=None,
            duration=None,
        ),
        streak_at_end=1,
    )
    snapshot = build_daily_report_snapshot(
        metrics,
        previous=None,
        generated_at=end,
        timezone_name="UTC",
    )

    result = await DisabledReportCommentator().comment(commentary_input_from_snapshot(snapshot))

    assert result is None
