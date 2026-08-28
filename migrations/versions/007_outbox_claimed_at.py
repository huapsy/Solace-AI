"""Add the outbox ``claimed_at`` column for the atomic-claim poller (REV-39).

Revision ID: 007_outbox_claimed_at
Revises: 006_extend_rls
Create Date: 2026-07-30

The transactional-outbox poller now CLAIMS records atomically (PENDING ->
PUBLISHING with a ``claimed_at`` stamp) instead of a bare ``SELECT ... FOR UPDATE
SKIP LOCKED`` whose row locks released the instant the connection returned to the
pool — which let two replicas' pollers claim the same rows and double-publish.
The claim + stale-claim reclaim need a ``claimed_at`` column and a supporting
index (see ``src/solace_events/postgres_stores.py``).

The ``event_outbox`` table is provisioned by the application at startup
(``PostgresOutboxStore.ensure_table`` / ``OUTBOX_TABLE_DDL``), NOT by Alembic, so:
  - fresh deployments get ``claimed_at`` from the updated DDL directly;
  - existing deployments get it from this idempotent ``ALTER ... IF EXISTS ... ADD
    COLUMN IF NOT EXISTS`` (a no-op if the app has not created the table yet — the
    updated DDL will include the column when it does).

Postgres-only; a no-op on SQLite (the outbox uses the in-memory store there).
"""
from __future__ import annotations

from alembic import context, op

# Alembic identifiers
revision = "007_outbox_claimed_at"
down_revision = "006_extend_rls"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = context.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        "ALTER TABLE IF EXISTS event_outbox "
        "ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_outbox_publishing_claimed "
        "ON event_outbox (claimed_at) WHERE status = 'PUBLISHING';"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS idx_outbox_publishing_claimed;")
    op.execute("ALTER TABLE IF EXISTS event_outbox DROP COLUMN IF EXISTS claimed_at;")
