"""Phase B / A-1b (P1): report generators must render REAL data, not zeros.

Two root causes made every report read zero (existing report tests only asserted keys
EXIST, never that values were non-zero):

1. Granularity: data is recorded in MINUTE windows but reports query HOUR/DAY -> the
   window-type filter dropped everything. Fixed by rollup in MetricsStore.get_aggregated.
2. Compliance metric-name mismatch: ComplianceAuditReportGenerator queried phantom names
   (events.safety.assessment.completed / events.safety.crisis.detected /
   events.safety.escalation.triggered) that nothing records. The aggregator records
   safety.assessments / safety.crisis_events / safety.escalations. Fixed by reconciling
   the compliance queries to the recorded names.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from reports import (
    SessionSummaryReportGenerator,
    SafetyOverviewReportGenerator,
    ClinicalOutcomesReportGenerator,
    ComplianceAuditReportGenerator,
    ReportTimeRange,
)


def _bracket_now(days_back: int = 1) -> ReportTimeRange:
    """A time range that firmly brackets 'now' (avoids minute-boundary flakiness)."""
    now = datetime.now(timezone.utc)
    return ReportTimeRange.custom(now - timedelta(days=days_back), now + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_session_summary_renders_real_counts(analytics_aggregator, sample_user_id) -> None:
    for _ in range(2):
        await analytics_aggregator.track_session_event(
            event_type="session.started", user_id=sample_user_id, session_id=uuid4(),
            metadata={"duration_seconds": 300},
        )
    await analytics_aggregator.track_session_event(
        event_type="session.ended", user_id=sample_user_id, session_id=uuid4(), metadata={},
    )

    report = await SessionSummaryReportGenerator().generate(analytics_aggregator, _bracket_now())

    assert report.summary["total_sessions"] == 2
    assert report.summary["completed_sessions"] == 1
    assert report.summary["avg_duration_minutes"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_safety_overview_renders_real_counts(analytics_aggregator) -> None:
    # 3 assessments (one per safety check); 2 of them escalate to a detected crisis.
    await analytics_aggregator.track_safety_assessment("LOW", 1)
    await analytics_aggregator.track_safety_assessment("HIGH", 2)
    await analytics_aggregator.track_safety_assessment("CRITICAL", 1)
    await analytics_aggregator.track_crisis_event("HIGH", 2)
    await analytics_aggregator.track_crisis_event("CRITICAL", 1)

    report = await SafetyOverviewReportGenerator().generate(analytics_aggregator, _bracket_now())

    assert report.summary["total_checks"] == 3
    assert report.summary["total_crises"] == 2  # two crisis.detected events
    assert report.summary["risk_distribution"].get("HIGH") == 1
    assert report.summary["risk_distribution"].get("LOW") == 1


@pytest.mark.asyncio
async def test_clinical_outcomes_renders_real_counts(analytics_aggregator) -> None:
    await analytics_aggregator.track_diagnosis_event(
        assessment_type="diagnosis.completed", severity="MILD", stepped_care_level=2
    )
    await analytics_aggregator.track_therapy_event(
        modality="CBT", technique="cognitive_restructuring", engagement_score=Decimal("0.8")
    )

    report = await ClinicalOutcomesReportGenerator().generate(analytics_aggregator, _bracket_now())

    assert report.summary["total_diagnoses"] == 1
    assert report.summary["total_interventions"] == 1
    assert report.summary["avg_engagement"] == pytest.approx(0.8)
    assert report.summary["modality_distribution"].get("CBT") == 1


@pytest.mark.asyncio
async def test_compliance_report_counts_real_crises_and_escalations(analytics_aggregator) -> None:
    # Two assessments; one incident escalates to a detected crisis + a triggered escalation.
    await analytics_aggregator.track_safety_assessment("LOW", 1)
    await analytics_aggregator.track_safety_assessment("CRITICAL", 1)
    await analytics_aggregator.track_crisis_event("CRITICAL", 1)
    await analytics_aggregator.track_escalation(priority="high", crisis_level="CRITICAL")

    report = await ComplianceAuditReportGenerator().generate(analytics_aggregator, _bracket_now())

    assert report.summary["total_safety_assessments"] == 2
    assert report.summary["total_crisis_detections"] == 1
    assert report.summary["total_escalations"] == 1
    assert report.summary["escalation_rate_pct"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_compliance_report_still_honest_about_unassessed_flags(analytics_aggregator) -> None:
    """A-1a regression: reconciling names must not re-introduce hardcoded compliance=True."""
    report = await ComplianceAuditReportGenerator().generate(analytics_aggregator, _bracket_now())
    comp = next(s for s in report.sections if "Compliance Status" in s.title)
    assert comp.data["data_retention_compliant"] == "not_assessed"
    assert comp.data["audit_log_integrity"] == "not_assessed"
