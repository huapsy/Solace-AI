"""Phase B / A-1b (P1) verification: full ingest -> store -> report path.

Proves the fix end-to-end through the REAL consumer (raw event dict ->
AnalyticsConsumer.process_event -> aggregator -> MetricsStore), not just the
aggregator's public API: a safety crisis event pushed through the consumer must
surface as non-zero data in the compliance and safety reports.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from reports import (
    ComplianceAuditReportGenerator,
    SafetyOverviewReportGenerator,
    SessionSummaryReportGenerator,
    ReportTimeRange,
)


def _raw(event_type: str, source: str, **payload) -> dict:
    return {
        "event_type": event_type,
        "user_id": str(uuid4()),
        "session_id": str(uuid4()),
        "metadata": {
            "event_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": str(uuid4()),
            "source_service": source,
        },
        **payload,
    }


def _bracket_now() -> ReportTimeRange:
    now = datetime.now(timezone.utc)
    return ReportTimeRange.custom(now - timedelta(days=1), now + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_crisis_event_through_consumer_surfaces_in_reports(
    analytics_consumer, analytics_aggregator
) -> None:
    # One CRITICAL incident emits the REAL canonical event shapes: an assessment
    # (recommended_action only "review") AND a crisis carrying escalation_action.
    ok1 = await analytics_consumer.process_event(
        _raw("session.started", "orchestrator-service")
    )
    ok2 = await analytics_consumer.process_event(
        _raw(
            "safety.assessment.completed", "safety-service",
            risk_level="CRITICAL", detection_layer=1, risk_score="0.95",
            recommended_action="review",
        )
    )
    ok3 = await analytics_consumer.process_event(
        _raw(
            "safety.crisis.detected", "safety-service",
            crisis_level="CRITICAL", detection_layer=1, confidence="0.95",
            escalation_action="escalate",
        )
    )
    # Auto-escalation emits the authoritative escalation event.
    ok4 = await analytics_consumer.process_event(
        _raw(
            "safety.escalation.triggered", "safety-service",
            priority="high", crisis_level="CRITICAL", notification_sent=True,
        )
    )
    assert ok1 and ok2 and ok3 and ok4

    time_range = _bracket_now()
    compliance = await ComplianceAuditReportGenerator().generate(analytics_aggregator, time_range)
    safety = await SafetyOverviewReportGenerator().generate(analytics_aggregator, time_range)
    session = await SessionSummaryReportGenerator().generate(analytics_aggregator, time_range)

    # One incident -> counted once per metric (no double count), escalation from crisis event.
    assert compliance.summary["total_safety_assessments"] == 1
    assert compliance.summary["total_crisis_detections"] == 1
    assert compliance.summary["total_escalations"] == 1
    assert safety.summary["total_crises"] == 1
    assert safety.summary["total_checks"] == 1
    assert session.summary["total_sessions"] == 1
