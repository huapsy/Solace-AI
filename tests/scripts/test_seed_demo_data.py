"""Tests for scripts/seed_demo_data.py — the synthetic staging/demo seed generator.

These tests prove the PURE generation path produces valid, domain-typed synthetic
records (including at least one CRITICAL crisis case) with NO database required, is
deterministic/idempotent from a seed, and that persistence refuses to run in
production.
"""
from __future__ import annotations

import importlib
from uuid import UUID

import pytest

# Import the module under test. Before scripts/seed_demo_data.py exists this import
# fails at collection time — that is the RED state.
seed = importlib.import_module("scripts.seed_demo_data")


def test_generate_demo_dataset_shape_and_count():
    """A dataset of N users carries non-empty, correctly-sized sub-collections."""
    ds = seed.generate_demo_dataset(count=3, seed=42)
    assert len(ds.users) == 3
    # Every domain surface is represented.
    assert ds.safety_assessments, "expected safety assessments"
    assert ds.diagnosis_sessions, "expected diagnosis sessions"
    assert ds.therapy_sessions, "expected therapy sessions"
    assert ds.personality_profiles, "expected personality profiles"
    assert ds.memory_entries, "expected memory entries"


def test_dataset_contains_at_least_one_critical_crisis_case():
    """The synthetic set must always include at least one CRITICAL crisis case."""
    ds = seed.generate_demo_dataset(count=3, seed=42)
    crisis = ds.crisis_cases()
    assert crisis, "expected at least one crisis case"
    assert any(a.crisis_level == "CRITICAL" for a in crisis)
    # Even with a single user, a CRITICAL case is guaranteed.
    tiny = seed.generate_demo_dataset(count=1, seed=1)
    assert any(a.crisis_level == "CRITICAL" for a in tiny.crisis_cases())


def test_records_are_valid_domain_instances():
    """Generated records reuse the services' real domain models, not ad-hoc dicts."""
    ds = seed.generate_demo_dataset(count=2, seed=7)

    user = ds.users[0]
    assert isinstance(user.user_id, UUID)
    assert user.email and "@" in user.email

    # Safety assessment is a real pydantic domain entity with a bounded risk score.
    sa = ds.safety_assessments[0]
    assert 0 <= float(sa.risk_score) <= 1
    assert sa.crisis_level in {"NONE", "LOW", "ELEVATED", "HIGH", "CRITICAL"}

    # OCEAN personality profile validates its own trait bounds on construction.
    prof = ds.personality_profiles[0]
    assert 0 <= float(prof.openness) <= 1

    # Memory entries carry content and a user linkage.
    mem = ds.memory_entries[0]
    assert mem.content
    assert isinstance(mem.user_id, UUID)


def test_generation_is_deterministic_and_idempotent():
    """Same seed => identical ids, so re-running the seed is safe (skip-if-exists)."""
    a = seed.generate_demo_dataset(count=3, seed=99)
    b = seed.generate_demo_dataset(count=3, seed=99)
    assert [u.user_id for u in a.users] == [u.user_id for u in b.users]
    assert [s.session_id for s in a.diagnosis_sessions] == [
        s.session_id for s in b.diagnosis_sessions
    ]
    # A different seed yields different ids.
    c = seed.generate_demo_dataset(count=3, seed=100)
    assert [u.user_id for u in a.users] != [u.user_id for u in c.users]


def test_generation_needs_no_database(monkeypatch):
    """The pure generation path must not read DATABASE_URL or touch any DB."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    ds = seed.generate_demo_dataset(count=2, seed=3)
    assert len(ds.users) == 2


def test_banner_declares_synthetic():
    assert "SYNTHETIC" in seed.SYNTHETIC_BANNER.upper()


def test_persist_refuses_in_production(monkeypatch):
    """Persistence is a dev/staging-only tool and must refuse under production."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    ds = seed.generate_demo_dataset(count=1, seed=1)
    with pytest.raises((RuntimeError, SystemExit)):
        seed.persist_dataset(ds, database_url="postgresql://ignored/db")


def test_dry_run_cli_returns_zero_without_db(monkeypatch, capsys):
    """--dry-run generates + prints and needs no DB and no writes."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    rc = seed.main(["--dry-run", "--count", "2", "--seed", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC" in out.upper()
