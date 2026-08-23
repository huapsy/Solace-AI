"""REV-17: analytics-service GDPR right-to-erasure.

Acceptance criteria encoded here:
  (a) InMemoryRepository.delete_user_data removes ALL of a user's rows (events)
      and leaves other users' rows untouched (idempotent);
  (b) ClickHouseRepository.delete_user_data issues the EXACT
      ``ALTER TABLE <t> DELETE WHERE user_id = %(user_id)s`` statement for each
      user-tagged table against a FAKE client (no live ClickHouse), binding
      user_id as a server-side param (injection-safe), and returns the counted
      rows; metrics/aggregations are NOT targeted;
  (c) the factory registers exactly the analytics store, register() is fail-loud
      on duplicates, and a failing store delete raises ``ErasureIncomplete``
      (never reported as complete; audit not emitted).

LIVE ClickHouse verification of the ALTER ... DELETE mutation is deferred to
Stage 3 (staging): these tests assert the CONSTRUCTED statements.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from repository import (
    ClickHouseConfig,
    ClickHouseRepository,
    InMemoryRepository,
    RepositoryConnectionError,
    USER_TAGGED_TABLES,
)
from models import AnalyticsEvent, TableName
from erasure import (
    ANALYTICS_CLICKHOUSE_STORE,
    build_user_data_erasure,
    erase_user_analytics_data,
)
from solace_infrastructure.gdpr import ErasureIncomplete, UserDataErasure


def _evt(user_id, ts):
    return AnalyticsEvent(
        event_id=uuid4(),
        event_type="session.started",
        category="session",
        user_id=user_id,
        timestamp=ts,
        correlation_id=uuid4(),
        source_service="test",
    )


# ---------------------------------------------------------------------------
# (a) InMemoryRepository delete removes ALL of a user's rows
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inmemory_delete_removes_all_user_rows():
    repo = InMemoryRepository()
    await repo.connect()
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    other = uuid4()

    await repo.insert_events_batch([_evt(user_id, now - timedelta(minutes=i)) for i in range(3)])
    await repo.insert_events_batch([_evt(other, now) for _ in range(2)])

    removed = await repo.delete_user_data(user_id)
    assert removed == 3

    remaining = await repo.query_events(
        start_time=now - timedelta(hours=1), end_time=now + timedelta(minutes=1)
    )
    assert len(remaining) == 2
    assert all(e.user_id == other for e in remaining)

    # Idempotent: a second erase for the same user removes nothing.
    assert await repo.delete_user_data(user_id) == 0


@pytest.mark.asyncio
async def test_inmemory_delete_matches_string_user_id():
    # erase() normalizes the id to a str before dispatch — the deleter must
    # still match the UUID-typed rows.
    repo = InMemoryRepository()
    await repo.connect()
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    await repo.insert_events_batch([_evt(user_id, now) for _ in range(2)])

    removed = await repo.delete_user_data(str(user_id))
    assert removed == 2


# ---------------------------------------------------------------------------
# (b) ClickHouse delete issues the exact ALTER ... DELETE statements (fake client)
# ---------------------------------------------------------------------------
_KNOWN_TABLES = (
    "analytics_events",
    "analytics_metrics",
    "analytics_aggregations",
    "analytics_sessions",
)


def _table_in(sql: str):
    for t in _KNOWN_TABLES:
        if t in sql:
            return t
    return None


class _FakeClickHouseClient:
    """Captures query()/command() calls; returns preset counts for count()."""

    def __init__(self, counts=None):
        self.queries: list[tuple[str, dict]] = []
        self.commands: list[tuple[str, dict]] = []
        self._counts = counts or {}

    def query(self, sql, parameters=None):
        self.queries.append((sql, parameters))
        n = self._counts.get(_table_in(sql), 0)
        return SimpleNamespace(result_rows=[[n]])

    def command(self, sql, parameters=None):
        self.commands.append((sql, parameters))
        return None


@pytest.mark.asyncio
async def test_clickhouse_delete_issues_alter_delete_for_each_user_tagged_table():
    repo = ClickHouseRepository(ClickHouseConfig())
    fake = _FakeClickHouseClient(counts={"analytics_events": 7})
    repo._client = fake
    repo._connected = True

    uid = uuid4()
    removed = await repo.delete_user_data(uid)

    # One ALTER ... DELETE mutation per user-tagged table, exact statement text.
    expected = [
        f"ALTER TABLE {t} DELETE WHERE user_id = %(user_id)s SETTINGS mutations_sync = 2"
        for t in USER_TAGGED_TABLES
    ]
    assert [sql for sql, _ in fake.commands] == expected
    assert fake.commands[0] == (
        "ALTER TABLE analytics_events DELETE WHERE user_id = %(user_id)s "
        "SETTINGS mutations_sync = 2",
        {"user_id": str(uid)},
    )

    # user_id is bound as a server-side param, never string-formatted (injection-safe).
    for sql, params in fake.commands:
        assert params == {"user_id": str(uid)}
        assert str(uid) not in sql

    # Rows-deleted count comes from the pre-mutation SELECT count().
    assert removed == 7


@pytest.mark.asyncio
async def test_clickhouse_delete_does_not_target_metrics_or_aggregations():
    # Anonymous aggregate tables carry no user_id and must NOT be touched.
    repo = ClickHouseRepository(ClickHouseConfig())
    fake = _FakeClickHouseClient(counts={"analytics_events": 1})
    repo._client = fake
    repo._connected = True

    await repo.delete_user_data(uuid4())

    all_sql = " ".join(sql for sql, _ in fake.commands)
    assert "analytics_metrics" not in all_sql
    assert "analytics_aggregations" not in all_sql
    # Documented invariant: only the events table is user-tagged.
    assert USER_TAGGED_TABLES == (TableName.EVENTS.value,)
    assert USER_TAGGED_TABLES == ("analytics_events",)


@pytest.mark.asyncio
async def test_clickhouse_delete_requires_connection():
    repo = ClickHouseRepository(ClickHouseConfig())
    with pytest.raises(RepositoryConnectionError):
        await repo.delete_user_data(uuid4())


# ---------------------------------------------------------------------------
# (c) factory registry + fail-loud
# ---------------------------------------------------------------------------
def test_factory_registers_exactly_analytics_store():
    erasure = build_user_data_erasure(InMemoryRepository())
    assert isinstance(erasure, UserDataErasure)
    assert erasure.store_names == (ANALYTICS_CLICKHOUSE_STORE,)
    assert ANALYTICS_CLICKHOUSE_STORE == "analytics_clickhouse"


def test_factory_register_is_fail_loud_on_duplicate():
    erasure = build_user_data_erasure(InMemoryRepository())
    with pytest.raises(ValueError):
        erasure.register(ANALYTICS_CLICKHOUSE_STORE, lambda uid: 0)


@pytest.mark.asyncio
async def test_erase_deletes_user_rows_and_returns_count():
    repo = InMemoryRepository()
    await repo.connect()
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    other = uuid4()
    await repo.insert_events_batch([_evt(user_id, now) for _ in range(4)])
    await repo.insert_events_batch([_evt(other, now) for _ in range(1)])

    report = await erase_user_analytics_data(user_id, repository=repo)

    assert report.complete is True
    assert report.failed == {}
    assert report.deleted[ANALYTICS_CLICKHOUSE_STORE] == 4
    assert report.total_deleted == 4

    remaining = await repo.query_events(
        start_time=now - timedelta(hours=1), end_time=now + timedelta(minutes=1)
    )
    assert all(e.user_id == other for e in remaining)


@pytest.mark.asyncio
async def test_erase_fires_audit_only_on_full_success():
    repo = InMemoryRepository()
    await repo.connect()
    now = datetime.now(timezone.utc)
    uid = uuid4()
    await repo.insert_events_batch([_evt(uid, now)])

    emitted: list = []
    report = await erase_user_analytics_data(
        uid, repository=repo, audit_emit=lambda r: emitted.append(r)
    )
    assert report.audit_emitted is True
    assert emitted and emitted[0] is report


class _FailingRepo:
    """Repository whose delete_user_data fails (e.g. ClickHouse outage)."""

    async def delete_user_data(self, user_id):  # noqa: ANN001
        raise RuntimeError("ClickHouse unreachable")


@pytest.mark.asyncio
async def test_erase_fails_loud_when_repo_delete_raises():
    with pytest.raises(ErasureIncomplete) as exc_info:
        await erase_user_analytics_data(uuid4(), repository=_FailingRepo())

    report = exc_info.value.report
    assert report.complete is False
    assert ANALYTICS_CLICKHOUSE_STORE in report.failed
    assert report.audit_emitted is False
