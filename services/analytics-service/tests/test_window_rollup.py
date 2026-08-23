"""Phase B / A-1b (P1): metric window-granularity rollup.

Root cause: MetricsStore.record() defaults to a MINUTE window and record_counter/
gauge/timing never override it, so EVERYTHING is stored in MINUTE buckets. But every
report generator (and get_dashboard_metrics) queries get_aggregated(window_type=HOUR|DAY),
and get_aggregated hard-filtered `bucket.window.window_type != window_type` -> a HOUR/DAY
query never matched a MINUTE bucket -> every report/dashboard metric read ZERO.

Fix: get_aggregated rolls finer-grained stored buckets up into the requested window
(grouped by window + labels), while keeping exact-granularity queries unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aggregations import AggregationWindow, MetricsStore


def _range(minutes_back: int = 60):
    now = datetime.now(timezone.utc)
    return now - timedelta(minutes=minutes_back), now + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_minute_counters_roll_up_to_hour_query(metrics_store: MetricsStore) -> None:
    """3 counter records (stored MINUTE) must be visible to an HOUR query."""
    for _ in range(3):
        await metrics_store.record_counter("events.session.started")

    start, end = _range()
    hour = await metrics_store.get_aggregated(
        "events.session.started",
        window_type=AggregationWindow.HOUR,
        start_time=start,
        end_time=end,
    )
    assert sum(m.count for m in hour) == 3, "MINUTE records must roll up into the HOUR query"


@pytest.mark.asyncio
async def test_minute_counters_roll_up_to_day_query(metrics_store: MetricsStore) -> None:
    """MINUTE records must be visible to a DAY query (used by clinical/compliance reports)."""
    for _ in range(5):
        await metrics_store.record_counter("diagnosis.assessments")

    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(minutes=1)
    day = await metrics_store.get_aggregated(
        "diagnosis.assessments",
        window_type=AggregationWindow.DAY,
        start_time=start,
        end_time=end,
    )
    assert sum(m.count for m in day) == 5


@pytest.mark.asyncio
async def test_rollup_preserves_labels(metrics_store: MetricsStore) -> None:
    """Rollup must keep per-label-set entries so risk/modality distributions still work."""
    await metrics_store.record_counter("safety.assessments", labels={"risk_level": "LOW"})
    await metrics_store.record_counter("safety.assessments", labels={"risk_level": "LOW"})
    await metrics_store.record_counter("safety.assessments", labels={"risk_level": "HIGH"})

    start, end = _range()
    hour = await metrics_store.get_aggregated(
        "safety.assessments",
        window_type=AggregationWindow.HOUR,
        start_time=start,
        end_time=end,
    )
    by_level = {m.labels.get("risk_level"): m.count for m in hour}
    assert by_level == {"LOW": 2, "HIGH": 1}


@pytest.mark.asyncio
async def test_rollup_aggregates_value_stats(metrics_store: MetricsStore) -> None:
    """Rolled-up window must sum values and preserve min/max across the finer buckets."""
    await metrics_store.record_gauge("therapy.engagement_score", Decimal("0.2"))
    await metrics_store.record_gauge("therapy.engagement_score", Decimal("0.8"))

    start, end = _range()
    hour = await metrics_store.get_aggregated(
        "therapy.engagement_score",
        window_type=AggregationWindow.HOUR,
        start_time=start,
        end_time=end,
    )
    assert len(hour) == 1
    m = hour[0]
    assert m.count == 2
    assert m.sum_value == Decimal("1.0")
    assert m.min_value == Decimal("0.2")
    assert m.max_value == Decimal("0.8")
    assert m.window == AggregationWindow.HOUR


@pytest.mark.asyncio
async def test_exact_minute_query_still_returns_minute_buckets(metrics_store: MetricsStore) -> None:
    """Backward-compat: an exact MINUTE query must still return the MINUTE bucket unchanged."""
    await metrics_store.record_gauge("test.gauge", Decimal("50.5"))

    minute = await metrics_store.get_aggregated(
        "test.gauge", window_type=AggregationWindow.MINUTE
    )
    assert len(minute) == 1
    assert minute[0].sum_value == Decimal("50.5")


@pytest.mark.asyncio
async def test_dashboard_metrics_report_nonzero(analytics_aggregator, sample_user_id) -> None:
    """get_dashboard_metrics (default HOUR query) must reflect just-recorded MINUTE data."""
    from uuid import uuid4

    await analytics_aggregator.track_session_event(
        event_type="session.started",
        user_id=sample_user_id,
        session_id=uuid4(),
        metadata={},
    )
    await analytics_aggregator.track_safety_assessment("CRITICAL", 1)
    await analytics_aggregator.track_crisis_event("CRITICAL", 1)

    dashboard = await analytics_aggregator.get_dashboard_metrics()
    assert dashboard["sessions_last_hour"] == 1
    assert dashboard["safety_checks_last_hour"] == 1
    assert dashboard["crisis_events_last_24h"] == 1
