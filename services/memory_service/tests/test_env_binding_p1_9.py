"""
Phase C P1-9 regression: the compose env-var name MUST match the runtime settings
env_prefix or the production fail-loud guards (PHI encryption / PHI log sanitizer,
main.py:54 & 97) become permanently unreachable.

The memory service's *runtime* gate is ``MemoryServiceAppSettings`` (src/main.py),
whose ``env_prefix="MEMORY_"`` binds the ``environment`` field from ``MEMORY_ENVIRONMENT``.
The base compose previously set ``MEMORY_SERVICE_ENV`` (a name that binds nothing), pinning
``settings.environment`` to the ``development`` default in EVERY environment.
"""
from __future__ import annotations

import pytest

from services.memory_service.src.main import MemoryServiceAppSettings


def test_environment_binds_from_memory_environment(monkeypatch):
    """The var the compose file must set (MEMORY_ENVIRONMENT) actually binds."""
    monkeypatch.setenv("MEMORY_ENVIRONMENT", "production")
    settings = MemoryServiceAppSettings()
    assert settings.environment == "production"


def test_legacy_compose_var_name_does_not_bind(monkeypatch):
    """Documents the bug: the old compose name (MEMORY_SERVICE_ENV) binds nothing,
    so the production guards would silently no-op."""
    monkeypatch.delenv("MEMORY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("MEMORY_SERVICE_ENV", "production")
    settings = MemoryServiceAppSettings()
    # Falls back to the field default because the name never binds.
    assert settings.environment == "development"
