"""Widen encrypted PHI columns from VARCHAR(n) to TEXT (REV-13).

Revision ID: 005_widen_phi_columns
Revises: 004_oauth_accounts
Create Date: 2026-07-24

Four PHI fields are encrypted at the application layer into a ``v1$`` envelope
(nonce + ciphertext + tag, base64) that is much longer than the plaintext. They
were declared on bounded ``VARCHAR(n)`` columns, so a sufficiently long value's
ciphertext silently overflows / is rejected at write time:

    diagnosis_records.primary_diagnosis   VARCHAR(300) -> TEXT
    therapeutic_events.title              VARCHAR(300) -> TEXT
    treatment_plans.primary_diagnosis     VARCHAR(200) -> TEXT
    treatment_plans.termination_reason    VARCHAR(500) -> TEXT

VARCHAR(n) -> TEXT is a metadata-only change in PostgreSQL (no table rewrite,
no data loss). Postgres-only; no-op on other dialects so SQLite test runs pass.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "005_widen_phi_columns"
down_revision = "004_oauth_accounts"
branch_labels = None
depends_on = None


# (table, column, original varchar length for downgrade)
_COLUMNS: list[tuple[str, str, int]] = [
    ("diagnosis_records", "primary_diagnosis", 300),
    ("therapeutic_events", "title", 300),
    ("treatment_plans", "primary_diagnosis", 200),
    ("treatment_plans", "termination_reason", 500),
]


def _is_postgres() -> bool:
    bind = context.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    for table, column, _length in _COLUMNS:
        op.alter_column(table, column, type_=sa.Text(), existing_nullable=None)


def downgrade() -> None:
    if not _is_postgres():
        return
    # Narrowing TEXT -> VARCHAR(n) would truncate any encrypted value longer than
    # the original bound, permanently corrupting the AES-GCM envelope (undecryptable).
    # Rather than silently LEFT()-truncate PHI, abort if any row would lose data.
    bind = context.get_bind()
    for table, column, length in _COLUMNS:
        max_len = bind.execute(
            sa.text(f"SELECT COALESCE(MAX(LENGTH({column})), 0) FROM {table}")
        ).scalar()
        if max_len and max_len > length:
            raise RuntimeError(
                f"Refusing to downgrade {table}.{column} to VARCHAR({length}): "
                f"a value of length {max_len} would be truncated and its encrypted "
                f"PHI permanently corrupted. Migrate/erase the affected rows first."
            )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR({length}) "
            f"USING {column}::VARCHAR({length});"
        )
