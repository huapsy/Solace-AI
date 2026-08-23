"""REV-17: analytics-service GDPR right-to-erasure wiring.

The analytics service persists one user-tagged store: the ClickHouse
``analytics_events`` table (raw per-user events, including their payloads). This
module registers that store's delete (``AnalyticsRepository.delete_user_data``)
with the shared, fail-loud :class:`UserDataErasure` orchestrator so a failed
store delete is NEVER reported as a completed erasure and a completion audit
fires only on full success.

This is additive: the delete SQL lives in ``repository.py``; the factory here
only adapts ``delete_user_data`` into a :data:`StoreDeleter` and registers it,
and :func:`erase_user_analytics_data` drives an erase through
``UserDataErasure.erase``.

Scope note — why one store: metrics (``analytics_metrics``) and aggregations
(``analytics_aggregations``) hold ANONYMOUS aggregate counters with no user_id
column (verified in models.py / consumer.py / aggregations.py), so they are not
personal data under GDPR Art. 17 and are correctly excluded (see
``repository.USER_TAGGED_TABLES``). Live ClickHouse verification of the mutation
is deferred to Stage 3 (staging); unit tests assert the constructed
``ALTER ... DELETE WHERE user_id`` statements against a fake client.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional
from uuid import UUID

from solace_infrastructure.gdpr import ErasureReport, UserDataErasure

if TYPE_CHECKING:
    from repository import AnalyticsRepository

# Registered store name for the analytics ClickHouse tables. The user-tagged
# ``analytics_events`` rows are removed by one repository call, so they are
# erased as a single named store.
ANALYTICS_CLICKHOUSE_STORE = "analytics_clickhouse"


def build_user_data_erasure(repository: "AnalyticsRepository") -> UserDataErasure:
    """Build the analytics service's GDPR right-to-erasure orchestrator.

    Registers exactly one store — the analytics ClickHouse tables — whose deleter
    wraps ``repository.delete_user_data`` and returns the rows-deleted count. The
    shared :class:`UserDataErasure` then guarantees the fail-loud contract: if the
    delete fails, :meth:`UserDataErasure.erase` raises ``ErasureIncomplete``
    carrying the partial report rather than reporting a completed erasure.
    """
    erasure = UserDataErasure()

    async def _delete_analytics_clickhouse(user_id: str) -> int:
        # erase() normalizes user_id to a str before dispatch; the repository
        # delete accepts str|UUID and binds it as the ClickHouse %(user_id)s
        # param, so pass the normalized value straight through.
        return int(await repository.delete_user_data(user_id))

    erasure.register(ANALYTICS_CLICKHOUSE_STORE, _delete_analytics_clickhouse)
    return erasure


async def erase_user_analytics_data(
    user_id: UUID | str,
    *,
    repository: "AnalyticsRepository",
    audit_emit: Optional[Callable[[ErasureReport], object]] = None,
) -> ErasureReport:
    """Erase one user's analytics data, driving everything through the orchestrator.

    This is the service-level erase entry point (the analog of a delete
    endpoint's / domain method's erase path): it builds the registry and runs
    :meth:`UserDataErasure.erase`, so a failed store delete raises
    ``ErasureIncomplete`` and the completion audit fires only on full success.
    """
    erasure = build_user_data_erasure(repository)
    return await erasure.erase(user_id, audit_emit=audit_emit)
