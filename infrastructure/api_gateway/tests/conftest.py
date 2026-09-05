"""Shared fixtures for the api_gateway test suite.

Secret-hardening made ``KongSettings.admin_token`` a required field (no default;
must be supplied via the ``KONG_ADMIN_TOKEN`` env var). The unit tests construct
``KongSettings()`` / ``KongConfig`` with defaults, so provide a throwaway token
here rather than weakening the model. Mirrors the personality-service
``TestConfigSettings`` fixture that reconciles the same drift.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _required_gateway_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy required hardened settings for construction-time tests.

    ``KongSettings.admin_token`` (KONG_ADMIN_TOKEN) and ``JWTConfig.secret_key``
    (JWT_SECRET_KEY) are required with no default; provide throwaway values.
    """
    monkeypatch.setenv("KONG_ADMIN_TOKEN", "test-kong-admin-token")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-0123456789abcdef")
