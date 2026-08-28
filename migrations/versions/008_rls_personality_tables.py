"""Extend Row-Level Security to the personality PHI tables (C.2 / A2).

Revision ID: 008_rls_personality
Revises: 007_outbox_claimed_at
Create Date: 2026-07-30

Migration 006 enabled RLS on the 9 tables written under an authenticated USER
request (``get_current_user`` sets the ``app.current_user_id`` GUC). It DEFERRED
``personality_*`` because those tables are written by ``get_current_service``
(service-to-service) handlers that carry the user id in the request BODY, not a
user token — so no GUC was set and RLS + FORCE would have rejected every write.

That prerequisite is now met: the personality service-auth endpoints (``/detect``,
``/style``, ``/adapt``) call ``set_current_user_id(request.user_id)`` and the GDPR
erase path scopes the GUC to the target user, so every read/write and delete of a
``personality_*`` row runs with the GUC set. This migration enables RLS + FORCE +
a per-user policy on the three personality PHI tables. All three carry a ``user_id``
column (ClinicalBase) that their INSERTs populate — verified in
``services/personality_service/src/infrastructure/postgres_repository.py`` — so the
``user_id``-keyed policy's WITH CHECK admits the writes.

SAFETY tables (``safety_assessments``/``safety_incidents``/``user_risk_profiles``/
``safety_plans``/``escalations``/``safety_events``) remain DEFERRED: several are
written by non-request paths (the escalation manager, Kafka consumers, background
workers) that do not set the GUC, so enabling RLS on them requires a per-writer GUC
audit on real Postgres first (a Stage-3 / B.3 task). Enabling them blind would lock
those writers out — the same conservative narrowing applied in migration 006.

Postgres-only; a no-op on SQLite (tests). Idempotent (DROP POLICY IF EXISTS before
CREATE). Enforcement is verified on real Postgres at B.3 (staging).
"""
from __future__ import annotations

from alembic import context, op

# Alembic identifiers
revision = "008_rls_personality"
down_revision = "007_outbox_claimed_at"
branch_labels = None
depends_on = None


# Personality PHI tables, all now written with the per-user GUC set. Each carries a
# ``user_id`` column populated by its INSERT (see the postgres repository).
_TABLES_WITH_RLS: list[str] = [
    "personality_profiles",
    "trait_assessments",
    "profile_snapshots",
]


def _is_postgres() -> bool:
    bind = context.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return

    for table in _TABLES_WITH_RLS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        policy_name = f"{table}_by_user"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table};")
        op.execute(
            f"""
            CREATE POLICY {policy_name} ON {table}
                USING (user_id = current_setting('app.current_user_id', true)::uuid)
                WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid);
            """
        )


def downgrade() -> None:
    if not _is_postgres():
        return

    for table in _TABLES_WITH_RLS:
        policy_name = f"{table}_by_user"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table};")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
