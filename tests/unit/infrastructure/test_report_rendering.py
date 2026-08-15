"""Report text, JSON round-trip and PNG renderer tests."""

import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from matplotlib import image as matplotlib_image

from smoke_runner.domain.clock import UtcInstant
from smoke_runner.domain.local_time import weekly_report_schedule
from smoke_runner.domain.metrics import build_weekly_metrics
from smoke_runner.domain.models import IntervalChange, SmokingSession, TargetInterval
from smoke_runner.domain.report_models import WeeklyChartPoint, build_weekly_report_snapshot
from smoke_runner.domain.timeline import build_timeline
from smoke_runner.infrastructure.report_rendering import (
    deserialize_report_snapshot,
    render_current_week_chart,
    render_history_chart,
    render_report_text,
)


def at(day: int, hour: int = 0) -> UtcInstant:
    return UtcInstant(datetime(2026, 8, day, hour, tzinfo=UTC))


def partial_week_snapshot():
    timezone = ZoneInfo("UTC")
    activated_at = at(12, 15)
    timeline = build_timeline(
        [
            SmokingSession(id=1, occurred_at=at(12, 16)),
            SmokingSession(id=2, occurred_at=at(13, 18)),
        ],
        [
            IntervalChange(
                id=1,
                effective_at=activated_at,
                interval=TargetInterval.hours(1),
            )
        ],
    )
    metrics = build_weekly_metrics(
        timeline,
        [],
        schedule=weekly_report_schedule(date(2026, 8, 16), timezone, activated_at=activated_at),
        activated_at=activated_at,
        timezone=timezone,
    )
    return build_weekly_report_snapshot(
        metrics,
        previous=None,
        history=[metrics],
        generated_at=at(16, 9),
        timezone_name="UTC",
    )


def test_weekly_snapshot_json_round_trip_preserves_typed_facts() -> None:
    snapshot = partial_week_snapshot()
    payload = json.loads(json.dumps(asdict(snapshot), ensure_ascii=False))

    restored = deserialize_report_snapshot(payload)

    assert restored == snapshot
    text = render_report_text(restored)
    assert "неполная неделя" in text
    assert "Итоги неполной и полной недели напрямую не сравниваются" in text
    assert "Среднее опережение: — (нарушений не было)" in text


def test_chart_renderer_distinguishes_missing_and_partial_data_in_phone_size_png() -> None:
    snapshot = partial_week_snapshot()

    current_png = render_current_week_chart(snapshot)
    history_png = render_history_chart(snapshot)

    for content in (current_png, history_png):
        assert content.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(content) > 20_000
        pixels = matplotlib_image.imread(BytesIO(content), format="png")
        assert pixels.shape[0] > pixels.shape[1]
        assert pixels.shape[1] >= 900


def test_long_history_keeps_every_week_point() -> None:
    snapshot = partial_week_snapshot()
    first = snapshot.history_chart[0]
    history = tuple(
        WeeklyChartPoint(
            start_local_date=(date(2026, 1, 4) + index * timedelta(days=7)).isoformat(),
            end_local_date_exclusive=(date(2026, 1, 11) + index * timedelta(days=7)).isoformat(),
            is_partial=index == 0,
            total_sessions=index,
            total_violations=index % 3,
            average_earliness=first.average_earliness,
            average_wake_to_first=first.average_wake_to_first,
        )
        for index in range(40)
    )
    long_snapshot = replace(snapshot, history_chart=history)

    content = render_history_chart(long_snapshot)

    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(long_snapshot.history_chart) == 40
