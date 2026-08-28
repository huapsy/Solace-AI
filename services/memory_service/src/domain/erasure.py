"""REV-17: memory-service GDPR right-to-erasure wiring.

The memory service holds a user's PHI across three stores it owns:

  * ``memory_postgres`` — the ``memory_records`` / ``session_summaries`` /
    ``user_facts`` / ``therapeutic_events`` tables
    (:meth:`MemoryPostgresRepository.delete_user_data`, returns a 4-tuple of
    per-table row counts);
  * ``memory_weaviate`` — the vector collections
    (:meth:`WeaviateRepository.delete_user_data`, returns the collection count);
  * ``memory_redis`` — the working-memory / session / context caches
    (:meth:`RedisCache.delete_user_cache`, returns the key count).

This factory registers each store's existing durable delete with the shared,
fail-loud :class:`~solace_infrastructure.gdpr.UserDataErasure` orchestrator so
that a failed store delete is NEVER reported as a completed erasure. It is purely
additive: the domain ``MemoryService.delete_user_data`` routes through
:meth:`UserDataErasure.erase`, preserving its existing ``None`` (HTTP 204)
response contract.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from solace_infrastructure.gdpr import UserDataErasure

# Stable store names — asserted by tests and surfaced in the erasure audit event.
MEMORY_POSTGRES_STORE = "memory_postgres"
MEMORY_WEAVIATE_STORE = "memory_weaviate"
MEMORY_REDIS_STORE = "memory_redis"


def build_user_data_erasure(
    *,
    postgres_repo: Any | None = None,
    weaviate_repo: Any | None = None,
    redis_cache: Any | None = None,
) -> UserDataErasure:
    """Build a :class:`UserDataErasure` registering the memory service's stores.

    Only the stores actually supplied are registered — a service running without
    (say) Weaviate does not attest a Weaviate erasure it could not perform. Each
    deleter receives the ``str`` user id from the orchestrator and adapts it to
    the ``UUID`` the underlying repositories expect, returning the number of rows
    / keys removed.
    """
    erasure = UserDataErasure()

    if postgres_repo is not None:
        async def _delete_postgres(user_id: str) -> int:
            # delete_user_data -> (records, summaries, facts, events)
            counts = await postgres_repo.delete_user_data(UUID(user_id))
            return int(sum(counts))

        erasure.register(MEMORY_POSTGRES_STORE, _delete_postgres)

    if weaviate_repo is not None:
        async def _delete_weaviate(user_id: str) -> int:
            return int(await weaviate_repo.delete_user_data(UUID(user_id)))

        erasure.register(MEMORY_WEAVIATE_STORE, _delete_weaviate)

    if redis_cache is not None:
        async def _delete_redis(user_id: str) -> int:
            return int(await redis_cache.delete_user_cache(UUID(user_id)))

        erasure.register(MEMORY_REDIS_STORE, _delete_redis)

    return erasure
