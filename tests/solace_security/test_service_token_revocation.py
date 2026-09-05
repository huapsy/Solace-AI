"""P2-3: get_current_service must reject a REVOKED service token.

get_current_service used the sync decode path (decode_token), whose best-effort
revocation check returns False against a RedisTokenBlacklist — so a blacklisted
service token was still accepted. It must route through the async Redis-aware
revocation check (decode_token_async), exactly like get_current_user.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from solace_security.auth import AuthSettings, InMemoryTokenBlacklist, JWTManager, TokenBlacklist
from solace_security.middleware import _get_jwt_manager, get_current_service

SECRET = "test-secret-key-32-bytes-long!!!"


class _AsyncOnlyBlacklist(TokenBlacklist):
    """Simulates RedisTokenBlacklist: async check works, sync check does not (base -> False)."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()

    async def add(self, jti: str, expires_at: datetime) -> None:
        self._revoked.add(jti)

    async def is_blacklisted(self, jti: str) -> bool:
        return jti in self._revoked
    # is_blacklisted_sync inherited -> returns False, like Redis.


def _creds(token: str) -> MagicMock:
    c = MagicMock()
    c.scheme = "Bearer"
    c.credentials = token
    return c


@pytest.mark.asyncio
async def test_revoked_service_token_is_rejected(monkeypatch):
    blacklist = _AsyncOnlyBlacklist()
    manager = JWTManager(AuthSettings(secret_key=SECRET), token_blacklist=blacklist)

    token = manager.create_service_token("orchestrator-service", permissions=["call:safety"])
    jti = manager.decode_token_sync(token).payload.jti
    await blacklist.add(jti, datetime.now(timezone.utc) + timedelta(hours=1))

    # Force the middleware to use our async-only blacklisted manager.
    _get_jwt_manager.cache_clear()
    monkeypatch.setattr(
        "solace_security.middleware._get_jwt_manager", lambda: manager
    )

    request = MagicMock()
    request.url.path = "/internal/sync"
    with pytest.raises(HTTPException) as exc:
        await get_current_service(request, _creds(token))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_service_token_still_accepted(monkeypatch):
    manager = JWTManager(AuthSettings(secret_key=SECRET), token_blacklist=InMemoryTokenBlacklist())
    token = manager.create_service_token("safety-service", permissions=["call:x"])

    _get_jwt_manager.cache_clear()
    monkeypatch.setattr(
        "solace_security.middleware._get_jwt_manager", lambda: manager
    )

    request = MagicMock()
    request.url.path = "/internal/sync"
    service = await get_current_service(request, _creds(token))
    assert service.service_name == "safety-service"
