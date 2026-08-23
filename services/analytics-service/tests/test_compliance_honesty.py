"""Phase B / A-1 (P1, HIPAA): the compliance audit report must not falsely assert compliance.

data_retention_compliant and audit_log_integrity were hardcoded True. Analytics cannot
itself verify retention or audit-chain integrity, so asserting True on a regulatory
compliance report is dangerous — it must report honestly (not-self-assessed) and defer
those checks to the retention job / audit service.
"""
from __future__ import annotations

import pytest

from reports import ComplianceAuditReportGenerator


@pytest.mark.asyncio
async def test_compliance_report_does_not_hardcode_compliance(
    analytics_aggregator, time_range_last_hour
) -> None:
    gen = ComplianceAuditReportGenerator()
    report = await gen.generate(analytics_aggregator, time_range_last_hour)

    comp = next(s for s in report.sections if "Compliance Status" in s.title)
    assert comp.data["data_retention_compliant"] is not True, (
        "compliance report must not hardcode data_retention_compliant=True"
    )
    assert comp.data["audit_log_integrity"] is not True, (
        "compliance report must not hardcode audit_log_integrity=True"
    )
