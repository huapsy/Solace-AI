"""Phase B Gate G2 re-hunt: safety metrics must count the REAL event shapes.

Two confirmed defects in the A-1b analytics change (caught because the first tests
injected values the real event plane never emits):

1. Double-count: a single CRITICAL incident emits BOTH `safety.assessment.completed`
   AND `safety.crisis.detected`; both routed through _handle_safety_event -> both called
   track_safety_event -> safety.assessments and safety.crisis_events each +2 per incident.
2. Escalations never recorded: the assessment event's recommended_action is only
   "monitor"/"review" (never "escalate"); the escalate signal lives on the crisis event's
   `escalation_action="escalate"` field, which the consumer never read -> escalation_rate
   pinned at 0%.

Fix: count each safety metric from its OWN canonical event type — assessments from
safety.assessment.completed, crises from safety.crisis.detected, escalations from the
crisis event's escalation_action.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from reports import ComplianceAuditReportGenerator, SafetyOverviewReportGenerator, ReportTimeRange


def _raw(event_type: str, **payload) -> dict:
    return {
        "event_type": event_type,
        "user_id": str(uuid4()),
        "session_id": str(uuid4()),
        "metadata": {
            "event_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "correlation_id": str(uuid4()),
            "source_service": "safety-service",
        },
        **payload,
    }


# Realistic canonical event shapes (mirror src/solace_events/schemas.py):
def _assessment(risk_level: str) -> dict:
    # SafetyAssessmentEvent: has risk_level + recommended_action ("monitor"/"review"),
    # NO escalation_action, NO crisis_level.
    return _raw("safety.assessment.completed", risk_level=risk_level,
                recommended_action="review", risk_score="0.9", detection_layer=1)


def _crisis(crisis_level: str, escalating: bool = False) -> dict:
    # CrisisDetectedEvent: has crisis_level + escalation_action, NO risk_level,
    # NO recommended_action.
    return _raw("safety.crisis.detected", crisis_level=crisis_level,
                escalation_action="escalate" if escalating else "monitor",
                detection_layer=1, confidence="0.95")


def _escalation(priority: str = "high", crisis_level: str = "CRITICAL") -> dict:
    # EscalationTriggeredEvent: the authoritative "escalation actually triggered" event,
    # emitted by both auto-escalation and the /escalate endpoint.
    return _raw("safety.escalation.triggered", priority=priority, crisis_level=crisis_level,
                escalation_reason="auto", notification_sent=True)


def _bracket_now() -> ReportTimeRange:
    now = datetime.now(timezone.utc)
    return ReportTimeRange.custom(now - timedelta(days=1), now + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_single_incident_not_double_counted(analytics_consumer, analytics_aggregator) -> None:
    # One CRITICAL incident => one assessment.completed + one crisis.detected.
    assert await analytics_consumer.process_event(_assessment("CRITICAL"))
    assert await analytics_consumer.process_event(_crisis("CRITICAL", escalating=True))

    tr = _bracket_now()
    compliance = await ComplianceAuditReportGenerator().generate(analytics_aggregator, tr)
    safety = await SafetyOverviewReportGenerator().generate(analytics_aggregator, tr)

    assert compliance.summary["total_safety_assessments"] == 1, "assessment counted once, not twice"
    assert compliance.summary["total_crisis_detections"] == 1, "crisis counted once, not twice"
    assert safety.summary["total_crises"] == 1
    assert safety.summary["total_checks"] == 1


@pytest.mark.asyncio
async def test_escalation_recorded_from_escalation_event(analytics_consumer, analytics_aggregator) -> None:
    # Escalations are counted from the authoritative safety.escalation.triggered event
    # (auto-escalation and /escalate both emit it), NOT from the crisis event's
    # escalation_action recommendation.
    assert await analytics_consumer.process_event(_assessment("CRITICAL"))
    assert await analytics_consumer.process_event(_crisis("CRITICAL", escalating=True))
    assert await analytics_consumer.process_event(_escalation(priority="high", crisis_level="CRITICAL"))

    compliance = await ComplianceAuditReportGenerator().generate(analytics_aggregator, _bracket_now())
    assert compliance.summary["total_escalations"] == 1
    assert compliance.summary["escalation_rate_pct"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_crisis_without_escalation_event_records_no_escalation(
    analytics_consumer, analytics_aggregator
) -> None:
    # A crisis whose escalation_action says "escalate" is only a RECOMMENDATION; with no
    # accompanying safety.escalation.triggered (e.g. auto-escalation disabled), no
    # escalation is counted.
    assert await analytics_consumer.process_event(_crisis("HIGH", escalating=True))
    compliance = await ComplianceAuditReportGenerator().generate(analytics_aggregator, _bracket_now())
    assert compliance.summary["total_crisis_detections"] == 1
    assert compliance.summary["total_escalations"] == 0


@pytest.mark.asyncio
async def test_direct_escalation_without_crisis_is_counted(analytics_consumer, analytics_aggregator) -> None:
    # A clinician-initiated /escalate emits ONLY safety.escalation.triggered (no crisis
    # event); it must still be counted (the old crisis-based counting missed this).
    assert await analytics_consumer.process_event(_escalation(priority="high", crisis_level="HIGH"))
    compliance = await ComplianceAuditReportGenerator().generate(analytics_aggregator, _bracket_now())
    assert compliance.summary["total_escalations"] == 1


@pytest.mark.asyncio
async def test_assessment_only_does_not_count_as_crisis(analytics_consumer, analytics_aggregator) -> None:
    # A CRITICAL assessment with no accompanying crisis event is an assessment, not a
    # crisis (crisis_events comes from the crisis.detected event only).
    assert await analytics_consumer.process_event(_assessment("CRITICAL"))
    safety = await SafetyOverviewReportGenerator().generate(analytics_aggregator, _bracket_now())
    assert safety.summary["total_checks"] == 1
    assert safety.summary["total_crises"] == 0
