"""Extend Row-Level Security to the remaining PHI tables (H-56 full / C.2).

Revision ID: 006_extend_rls
Revises: 005_widen_phi_columns
Create Date: 2026-07-28

Migration 002 enabled PostgreSQL Row-Level Security on three clinical tables as
the MVP cut. This migration extends the same per-user policy to the remaining PHI
tables that are **written within an authenticated USER request** — i.e. whose
handlers use ``get_current_user`` so the RLS GUC ``app.current_user_id`` is set by
``src/solace_common/request_context.py`` + ``ConnectionPoolManager.acquire``.

CRITICAL SCOPING (why not all user_id tables): RLS + ``FORCE ROW LEVEL SECURITY``
means any writer that does NOT set the GUC has its INSERTs rejected (WITH CHECK on a
NULL GUC) and its SELECTs return nothing. Several PHI tables are written by paths that
do NOT currently set the GUC, so they are **intentionally deferred** until those paths
propagate the user id into the GUC (or run under a ``BYPASSRLS`` role):

  - safety_* (safety_assessments/plans, risk_factors, contraindication_checks,
    safety_events) and personality_* (personality_profiles, trait_assessments,
    profile_snapshots) — written by ``get_current_service`` (service-to-service) handlers
    that carry the user id in the request BODY, not a user token; they need a
    service-side ``set_current_user_id(request.user_id)`` before RLS can be enabled.
  - session_summaries, therapeutic_events, memory_user_profiles — written by background
    memory-consolidation workers with no user request context.
  - notifications, escalations — written by the notification/escalation consumers.
  - oauth_accounts — written pre-authentication (no user context yet).
  - delivery_attempts and other join-only tables — need a join-based policy.

Tracked as a C.2 follow-up (verify on real Postgres at B.3). Each policy keys off the
GUC; a connection without it set sees nothing (the safe default). Admin/maintenance uses
``BYPASSRLS``. This migration is a no-op on non-PostgreSQL dialects (SQLite test runs) and
is idempotent (``DROP POLICY IF EXISTS`` before ``CREATE POLICY``).
"""
from __future__ import annotations

from alembic import context, op

# Alembic identifiers
revision = "006_extend_rls"
down_revision = "005_widen_phi_columns"
branch_labels = None
depends_on = None


# Tables written under an authenticated USER request (get_current_user -> GUC set),
# so RLS + FORCE RLS is safe. See the module docstring for the deferred tables + why.
_TABLES_WITH_RLS: list[str] = [
    # diagnosis_service (get_current_user)
    "diagnosis_symptoms",
    "diagnosis_hypotheses",
    "diagnosis_records",
    # therapy_service (get_current_user)
    "treatment_plans",
    "therapy_interventions",
    "homework_assignments",
    # user-service (get_current_user, /users/me/*)
    "consent_records",
    "user_preferences",
    "user_notification_preferences",
]


def _is_postgres() -> bool:
    """Check we're on Postgres. RLS statements are Postgres-only."""
    bind = context.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        # Skip silently on SQLite / other dialects. Tests run on SQLite.
        return

    for table in _TABLES_WITH_RLS:
        # Enable RLS on the table. Existing rows remain queryable only
        # under matching policies.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        # Force RLS even for the table owner (matters when the service runs
        # with the owning role rather than a separate app role).
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

        # Per-user access policy. Relies on the per-request GUC
        # ``app.current_user_id`` set by middleware. The cast to uuid
        # normalizes whatever the middleware sets (string uuid format).
        # DROP-then-CREATE keeps upgrade() idempotent (CREATE POLICY has no IF NOT EXISTS).
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
