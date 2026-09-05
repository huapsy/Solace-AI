"""REV-32 (code half): a service resolves ``settings.environment`` from its OWN prefixed
var (e.g. SAFETY_ENVIRONMENT / MEMORY_ENVIRONMENT). If that disagrees with the bare
``ENVIRONMENT`` the deployment set, the service can silently run as ``development`` while
the platform believes it is ``production`` — the production fail-loud guards then never
engage. ``ProductionGuards.assert_environment_consistency`` fails loud in production/staging
when the two disagree, and is a no-op when either side is unset (dev convenience).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from solace_security.production_guards import (
    ProductionGuards,
    ProductionSecurityError,
)


def test_mismatch_in_production_raises(monkeypatch):
    """Bare ENVIRONMENT=production but the service resolved development => fail loud."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ProductionSecurityError):
        ProductionGuards.assert_environment_consistency(
            "development", service_name="safety-service"
        )


def test_service_says_production_but_bare_dev_raises(monkeypatch):
    """The reverse mismatch is equally dangerous and must also fail loud."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    with pytest.raises(ProductionSecurityError):
        ProductionGuards.assert_environment_consistency("production")


def test_matching_environments_pass(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    ProductionGuards.assert_environment_consistency("production")  # no raise


def test_matching_case_insensitive(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "Production")
    ProductionGuards.assert_environment_consistency("production")  # no raise


def test_no_bare_environment_is_noop(monkeypatch):
    """When the bare var is unset there is nothing to reconcile against (dev)."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    ProductionGuards.assert_environment_consistency("development")  # no raise


def test_service_env_none_is_noop(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    ProductionGuards.assert_environment_consistency(None)  # no raise


def test_non_production_mismatch_warns_not_raises(monkeypatch):
    """A dev/staging-free mismatch (e.g. test vs development) must not crash startup."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    ProductionGuards.assert_environment_consistency("development")  # no raise
