"""Tests for the Redis hot-path metering layer.

Covers:

* Cache semantics: GET hit / miss / malformed / transport error, with
  SQLite rehydration on miss and fall-back on transport error.
* The gate-before-reconstruct invariant (SR-03): an over-cap request is
  denied BEFORE ``reconstruct_key`` / ``reconstruct_key_fp`` are called.
  Proven by patching both on ``worthless.proxy.app`` and asserting the
  mocks were never invoked after a 402.
* Dual-phase ``record_spend``: SQLite (authoritative) + Redis INCR
  (best-effort).
* No-regression: when Redis is not configured, the SQLite gate and SQLite
  ledger behave exactly as pre-Redis.

Uses an in-memory Redis stub so the tests do not require a running Redis.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest

from worthless.proxy.app import create_app
from worthless.proxy.config import ProxySettings
from worthless.proxy.errors import spend_cap_error_response
from worthless.proxy.metering import (
    SPEND_KEY_PREFIX,
    RedisValueError,
    get_spend_hot,
    incr_spend_hot,
    record_spend,
    rehydrate_spend_hot,
    spend_key,
    sum_spend_sqlite,
)
from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule
from worthless.storage.repository import StoredShard
from worthless.storage.schema import SCHEMA


# ---------------------------------------------------------------------------
# In-memory Redis stub.
# Implements only GET / SET (with NX) / INCRBY / aclose — the surface the
# proxy actually uses. Values are stored as bytes to match real redis-py
# behaviour when decode_responses=False.
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.closed = False

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any, *, nx: bool = False, **_: Any) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = str(value).encode() if not isinstance(value, bytes) else value
        return True

    async def incrby(self, key: str, amount: int) -> int:
        current = int(self.store[key]) if key in self.store else 0
        current += int(amount)
        self.store[key] = str(current).encode()
        return current

    async def aclose(self) -> None:
        self.closed = True


class _ExplodingRedis:
    """All operations raise — exercises the transport-error fallback path."""

    async def get(self, key: str) -> bytes | None:  # noqa: ARG002
        raise ConnectionError("redis is down")

    async def set(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ARG002
        raise ConnectionError("redis is down")

    async def incrby(self, key: str, amount: int) -> int:  # noqa: ARG002
        raise ConnectionError("redis is down")


class _PlantedRedis:
    """Stores a single malformed value — exercises tamper handling."""

    def __init__(self, key: str, bad_value: bytes) -> None:
        self.key = key
        self.bad_value = bad_value
        self.set_calls: list[tuple[str, Any, bool]] = []

    async def get(self, key: str) -> bytes | None:
        return self.bad_value if key == self.key else None

    async def set(self, key: str, value: Any, *, nx: bool = False, **_: Any) -> bool:
        self.set_calls.append((key, value, nx))
        return True

    async def incrby(self, key: str, amount: int) -> int:  # noqa: ARG002
        return 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sqlite_db(tmp_path):
    db_path = tmp_path / "worthless.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()

    conn = await aiosqlite.connect(db_path)
    yield conn, str(db_path)
    await conn.close()


async def _configure_cap(db: aiosqlite.Connection, alias: str, cap: float | None) -> None:
    await db.execute(
        "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
        (alias, cap),
    )
    await db.commit()


async def _record_tokens(db_path: str, alias: str, tokens: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            (alias, tokens, None, "openai"),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Redis helpers — new contract
# ---------------------------------------------------------------------------


def test_spend_key_namespace():
    assert spend_key("alice") == f"{SPEND_KEY_PREFIX}alice"
    assert spend_key("proj-1").startswith(SPEND_KEY_PREFIX)


@pytest.mark.asyncio
async def test_get_spend_hot_missing_key_returns_none():
    """Cache miss → None, NOT 0. 0 is a valid stored value."""
    r = _FakeRedis()
    assert await get_spend_hot(r, "nobody") is None


@pytest.mark.asyncio
async def test_get_spend_hot_hit_returns_int():
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 123)
    assert await get_spend_hot(r, "alice") == 123


@pytest.mark.asyncio
async def test_get_spend_hot_malformed_raises():
    """Tamper/corruption must not silently read as 0."""
    r = _PlantedRedis(key=spend_key("alice"), bad_value=b"not-a-number")
    with pytest.raises(RedisValueError):
        await get_spend_hot(r, "alice")


@pytest.mark.asyncio
async def test_incr_spend_hot_is_atomic_and_cumulative():
    r = _FakeRedis()
    assert await incr_spend_hot(r, "alice", 100) == 100
    assert await incr_spend_hot(r, "alice", 50) == 150
    assert await get_spend_hot(r, "alice") == 150


@pytest.mark.asyncio
async def test_incr_spend_hot_ignores_non_positive():
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 0)
    await incr_spend_hot(r, "alice", -5)
    # Key was never written; get returns None, not 0.
    assert await get_spend_hot(r, "alice") is None


@pytest.mark.asyncio
async def test_sum_spend_sqlite_reads_ledger(sqlite_db):
    db, db_path = sqlite_db
    await _record_tokens(db_path, "alice", 40)
    await _record_tokens(db_path, "alice", 60)
    await _record_tokens(db_path, "bob", 999)
    assert await sum_spend_sqlite(db, "alice") == 100
    assert await sum_spend_sqlite(db, "nobody") == 0


@pytest.mark.asyncio
async def test_rehydrate_warms_counter_from_sqlite(sqlite_db):
    db, db_path = sqlite_db
    await _record_tokens(db_path, "alice", 777)
    r = _FakeRedis()

    total = await rehydrate_spend_hot(r, db, "alice")
    assert total == 777
    # The counter is now warmed and subsequent GET returns the SQLite total.
    assert await get_spend_hot(r, "alice") == 777


@pytest.mark.asyncio
async def test_rehydrate_uses_nx_does_not_clobber_fresher_writer(sqlite_db):
    """If a concurrent INCR landed before our warmer SET, keep the fresher value."""
    db, db_path = sqlite_db
    await _record_tokens(db_path, "alice", 100)
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 250)  # concurrent writer already landed

    total = await rehydrate_spend_hot(r, db, "alice")
    # rehydrate returns the SQLite SUM (100), but the Redis SET was NX and
    # did not clobber the fresher 250.
    assert total == 100
    assert await get_spend_hot(r, "alice") == 250


# ---------------------------------------------------------------------------
# record_spend — dual-phase behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_spend_writes_sqlite_and_increments_redis(sqlite_db):
    _, db_path = sqlite_db
    r = _FakeRedis()

    await record_spend(db_path, "alice", 42, "gpt-4o-mini", "openai", redis=r)

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT tokens, model, provider FROM spend_log WHERE key_alias = ?",
            ("alice",),
        ) as cur:
            row = await cur.fetchone()
    assert row == (42, "gpt-4o-mini", "openai")
    assert await get_spend_hot(r, "alice") == 42


@pytest.mark.asyncio
async def test_record_spend_without_redis_is_sqlite_only(sqlite_db):
    """No regression: unset WORTHLESS_REDIS_URL → pre-Redis behaviour."""
    _, db_path = sqlite_db

    await record_spend(db_path, "alice", 10, None, "openai", redis=None)

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
            ("alice",),
        ) as cur:
            (total,) = await cur.fetchone()  # type: ignore[assignment]
    assert total == 10


@pytest.mark.asyncio
async def test_record_spend_swallows_redis_failure(sqlite_db):
    """SQLite is authoritative. A Redis INCR failure must not raise."""
    _, db_path = sqlite_db

    await record_spend(db_path, "alice", 7, None, "openai", redis=_ExplodingRedis())

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
            ("alice",),
        ) as cur:
            (total,) = await cur.fetchone()  # type: ignore[assignment]
    assert total == 7


# ---------------------------------------------------------------------------
# SpendCapRule — new hot-path semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_under_cap_allows(sqlite_db):
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 500)

    rule = SpendCapRule(db=db, redis=r)
    assert await rule.evaluate("alice", object(), provider="openai", body=b"") is None


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_over_cap_denies(sqlite_db):
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 1500)

    rule = SpendCapRule(db=db, redis=r)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == spend_cap_error_response(provider="openai").status_code


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_miss_rehydrates_from_sqlite_and_denies(sqlite_db):
    """Cold start / restart / eviction: Redis empty, SQLite has history.

    The old code silently allowed the request (counter read as 0). The fix
    rehydrates from SQLite before deciding.
    """
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 2_000)  # already over cap in ledger
    r = _FakeRedis()  # cache is empty

    rule = SpendCapRule(db=db, redis=r)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")

    assert result is not None, (
        "Cache miss + over-cap SQLite history MUST deny. Returning 0 on miss "
        "would silently bypass the cap on cold start / restart / eviction."
    )
    assert result.status_code == 402
    # And the counter is now warm for the next request.
    assert await get_spend_hot(r, "alice") == 2_000


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_miss_rehydrates_and_allows_when_under_cap(sqlite_db):
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 400)
    r = _FakeRedis()

    rule = SpendCapRule(db=db, redis=r)
    assert await rule.evaluate("alice", object(), provider="openai", body=b"") is None
    assert await get_spend_hot(r, "alice") == 400


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_malformed_value_rehydrates(sqlite_db):
    """Planted bogus value must not read as 0 — rehydrate from SQLite AND
    overwrite the corrupt key (CodeRabbit major finding on rules.py:206).

    Earlier this test only asserted a 402 — but that passes whether or not
    Redis was healed. The real invariant is that the next request also
    sees a clean counter, which requires force=True on the rehydrate.
    Without that, SET NX no-ops on the existing corrupt key and Redis
    stays permanently broken (every request pays the SQLite SUM cost).
    """
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 5_000)

    # Use FakeRedis (which has a working SET) and plant a tampered value
    # directly — _PlantedRedis's set() is a no-op so it can't observe the
    # overwrite-or-not behaviour we care about here.
    r = _FakeRedis()
    r.store[spend_key("alice")] = b"tampered"

    rule = SpendCapRule(db=db, redis=r)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402

    # The tampered value must have been overwritten with the SQLite SUM.
    raw = r.store.get(spend_key("alice"))
    assert raw == b"5000", (
        f"Tampered Redis value was NOT overwritten — Redis stuck on {raw!r}. "
        "rehydrate_spend_hot must use force=True on the RedisValueError path."
    )
    # And a follow-up evaluate sees the clean counter (not RedisValueError).
    assert await get_spend_hot(r, "alice") == 5_000


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_transport_error_falls_back_to_sqlite(sqlite_db):
    """Redis outage → gate stays up via SQLite. No 402-storm.

    SR-03 holds: reconstruction is gated on the return value, and the SQLite
    path is itself fail-closed on DB error.
    """
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 300)  # well under cap

    rule = SpendCapRule(db=db, redis=_ExplodingRedis())
    # Under cap → allow even with Redis dead.
    assert await rule.evaluate("alice", object(), provider="openai", body=b"") is None


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_transport_error_still_denies_over_cap(sqlite_db):
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 5_000)

    rule = SpendCapRule(db=db, redis=_ExplodingRedis())
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402


@pytest.mark.asyncio
async def test_spend_cap_rule_without_redis_uses_sqlite(sqlite_db):
    """No regression: redis=None preserves the pre-Redis SQLite path."""
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 2_000)

    rule = SpendCapRule(db=db, redis=None)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402


# ---------------------------------------------------------------------------
# WOR-242 × Redis — reservation mechanism across the hot path.
#
# The merge of feat/redis-metering with origin/main (WOR-242) composed two
# mechanisms: Redis counter (committed total) + in-memory reservation
# (in-flight total). The merged contract — same as the pre-Redis SQLite
# path — is soft: a request passes whenever committed + reserved < cap,
# reserving min(estimate, remaining_budget). Concurrent bursts can still
# overrun by up to `_estimate_tokens × concurrency`; the committed counter
# catches up via record_spend and denies subsequent requests. See the
# SpendCapRule docstring.
#
# These tests cover that contract explicitly under the Redis backend —
# previously only fakeredis under-cap / over-cap tests existed, so the
# reservation path on the Redis side was never exercised.
# ---------------------------------------------------------------------------


import asyncio  # noqa: E402


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_reservation_denies_when_committed_plus_reserved_at_cap(
    sqlite_db,
):
    """Concrete effective-total check: committed + reserved >= cap → deny.

    Seed the Redis counter at the cap value and ensure a new request is
    denied regardless of the requested size. Without the Redis path
    applying the reservation lock check, this would pass-through.
    """
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 1000)  # already at cap

    rule = SpendCapRule(db=db, redis=r)
    body = b'{"model":"gpt-4","max_tokens":10}'
    result = await rule.evaluate("alice", object(), provider="openai", body=body)
    assert result is not None
    assert result.status_code == 402


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_reservation_denies_when_prior_reservation_fills_cap(
    sqlite_db,
):
    """After one request reserves the whole remaining budget, a second is denied.

    Cap=1000, committed=0. Place a reservation directly via a first
    evaluate with max_tokens=1000 — that reserves all 1000. The second
    call sees committed(0) + reserved(1000) >= cap → deny.
    """
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()
    rule = SpendCapRule(db=db, redis=r)

    body_full = b'{"model":"gpt-4","max_tokens":1000}'
    assert await rule.evaluate("alice", object(), provider="openai", body=body_full) is None

    body_small = b'{"model":"gpt-4","max_tokens":50}'
    result = await rule.evaluate("alice", object(), provider="openai", body=body_small)
    assert result is not None
    assert result.status_code == 402


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_release_reservation_frees_budget(sqlite_db):
    """release_reservation makes the budget available for a follow-up request."""
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()
    rule = SpendCapRule(db=db, redis=r)
    body_full = b'{"model":"gpt-4","max_tokens":1000}'

    # Reserve the whole budget.
    assert await rule.evaluate("alice", object(), provider="openai", body=body_full) is None
    # At cap via reservation → deny.
    assert (await rule.evaluate("alice", object(), provider="openai", body=body_full)) is not None

    # Release the reservation (simulates upstream call completing).
    await rule.release_reservation("alice", 1000)

    # Budget restored.
    assert await rule.evaluate("alice", object(), provider="openai", body=body_full) is None


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_committed_plus_reservation_combine(sqlite_db):
    """Both committed counter and reservations contribute to the effective total.

    Prevents the regression where either mechanism silently shadows the
    other on the Redis path.
    """
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 600)  # prior committed spend

    rule = SpendCapRule(db=db, redis=r)

    # max_tokens=400 fills the remaining budget via reservation.
    body_400 = b'{"model":"gpt-4","max_tokens":400}'
    assert await rule.evaluate("alice", object(), provider="openai", body=body_400) is None

    # Effective total is now 600 committed + 400 reserved = 1000 = cap → deny.
    body_any = b'{"model":"gpt-4","max_tokens":1}'
    result = await rule.evaluate("alice", object(), provider="openai", body=body_any)
    assert result is not None
    assert result.status_code == 402


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_reservation_rehydrates_on_cache_miss(sqlite_db):
    """Cache miss → rehydrate from SQLite → reservation check uses the rehydrated counter.

    Without this, a miss would run the reservation-lock branch with
    counter=None/0 and let an over-cap alias bypass on the very first
    request after cold start.
    """
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 1000)  # already at cap in ledger
    r = _FakeRedis()  # cold

    rule = SpendCapRule(db=db, redis=r)
    body = b'{"model":"gpt-4","max_tokens":1}'
    result = await rule.evaluate("alice", object(), provider="openai", body=body)
    assert result is not None
    assert result.status_code == 402
    # Side effect: the counter is now warmed.
    assert await get_spend_hot(r, "alice") == 1000


@pytest.mark.asyncio
async def test_spend_cap_rule_redis_concurrent_requests_bounded_by_reservation(sqlite_db):
    """PoC-level bound: N concurrent requests each reserve at most the remaining budget.

    This is the weaker contract WOR-242 actually delivers — a hard
    boundary would require a Lua/CAS reserve in Redis. The test
    documents what the current mechanism DOES guarantee:

    * at least one of the two concurrent requests passes
    * the cumulative reservation never exceeds the cap
    * the SECOND request to reserve sees the first's reservation
    """
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()

    rule = SpendCapRule(db=db, redis=r)
    body = b'{"model":"gpt-4","max_tokens":800}'

    r1, r2 = await asyncio.gather(
        rule.evaluate("alice", object(), provider="openai", body=body),
        rule.evaluate("alice", object(), provider="openai", body=body),
    )
    # Both pass: first reserves 800, second reserves the remaining 200.
    assert r1 is None and r2 is None
    # Invariant: cumulative reservation equals cap (800 + 200 = 1000).
    assert rule._reserved["alias" if False else "alice"] == 1000

    # A THIRD concurrent request now finds committed(0) + reserved(1000) ≥ cap → deny.
    third = await rule.evaluate("alice", object(), provider="openai", body=body)
    assert third is not None
    assert third.status_code == 402


# ---------------------------------------------------------------------------
# Gate-before-reconstruct invariant (SR-03) — REAL app pipeline.
#
# The sentinel-rule test that lived here previously only proved the rules
# engine short-circuits — a property already covered by test_rules.py. The
# actual invariant is that ``reconstruct_key`` / ``reconstruct_key_fp`` are
# never called when the gate denies. Prove it by patching both functions
# on ``worthless.proxy.app`` and driving a real HTTP request through the
# ASGI app.
# ---------------------------------------------------------------------------


@pytest.fixture()
async def over_cap_proxy_app(tmp_db_path: str, fernet_key: bytes, repo):
    """App wired with a Redis-backed SpendCapRule whose counter is over cap."""
    from worthless.crypto.splitter import split_key_fp

    # Enroll a fake OpenAI key so fetch_encrypted returns a real row.
    alias = "test-alias"
    api_key = "sk-test-" + "x" * 40
    sr = split_key_fp(api_key, prefix="sk-", provider="openai")
    shard = StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider="openai",
    )
    await repo.store(alias, shard, prefix=sr.prefix, charset=sr.charset)
    shard_a_utf8 = sr.shard_a.decode("utf-8")

    # Configure the cap.
    async with aiosqlite.connect(tmp_db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 1000.0),
        )
        await wdb.commit()

    settings = ProxySettings(
        db_path=tmp_db_path,
        fernet_key=bytearray(fernet_key),
        default_rate_limit_rps=100.0,
        upstream_timeout=10.0,
        streaming_timeout=30.0,
        allow_insecure=True,
    )
    app = create_app(settings)

    db = await aiosqlite.connect(tmp_db_path)
    redis = _FakeRedis()
    await incr_spend_hot(redis, alias, 5_000)  # well over cap

    app.state.db = db
    app.state.repo = repo
    app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
    app.state.redis = redis
    app.state.rules_engine = RulesEngine(
        rules=[
            SpendCapRule(db=db, redis=redis),
            RateLimitRule(default_rps=settings.default_rate_limit_rps),
        ]
    )

    yield app, alias, shard_a_utf8

    await app.state.httpx_client.aclose()
    await db.close()


@pytest.mark.asyncio
async def test_gate_before_reconstruct_real_pipeline(over_cap_proxy_app, monkeypatch):
    """SR-03: an over-cap request must never reach reconstruct_key / _fp.

    Patches both reconstruction functions on the module where ``proxy_request``
    calls them. If either is invoked, the test fails — that is the invariant.
    """
    app, alias, shard_a = over_cap_proxy_app

    def _must_not_be_called(*args: Any, **kwargs: Any):
        raise AssertionError(
            "Gate-before-reconstruct invariant violated: reconstruct was "
            "called after a denied request."
        )

    reconstruct_mock = AsyncMock(side_effect=_must_not_be_called)
    reconstruct_fp_mock = AsyncMock(side_effect=_must_not_be_called)
    monkeypatch.setattr("worthless.proxy.app.reconstruct_key", reconstruct_mock)
    monkeypatch.setattr("worthless.proxy.app.reconstruct_key_fp", reconstruct_fp_mock)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/{alias}/v1/chat/completions",
            headers={"Authorization": f"Bearer {shard_a}"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 402, (
        f"Expected 402 from spend cap, got {resp.status_code}: {resp.text}"
    )
    assert reconstruct_mock.await_count == 0, "reconstruct_key was called on a denied request"
    assert reconstruct_fp_mock.await_count == 0, "reconstruct_key_fp was called on a denied request"


@pytest.mark.asyncio
async def test_gate_before_reconstruct_survives_redis_outage(over_cap_proxy_app, monkeypatch):
    """Redis outage + over-cap SQLite ledger → deny via SQLite fallback.

    Verifies the fail-back-to-SQLite path still honours SR-03: reconstruct
    is not called, and the request gets a 402 (from the SQLite SUM), not
    a 500 or hang.
    """
    app, alias, shard_a = over_cap_proxy_app

    # Seed the authoritative ledger over cap so the SQLite fallback denies.
    settings: ProxySettings = app.state.settings
    await _record_tokens(settings.db_path, alias, 9_000)

    # Replace the Redis on app.state with an exploding one — AND rewire the
    # rule to use it (the rule captured the old redis by reference).
    app.state.redis = _ExplodingRedis()
    app.state.rules_engine.rules[0] = SpendCapRule(db=app.state.db, redis=app.state.redis)

    def _must_not_be_called(*args: Any, **kwargs: Any):
        raise AssertionError("reconstruct was called on a denied request")

    reconstruct_mock = AsyncMock(side_effect=_must_not_be_called)
    reconstruct_fp_mock = AsyncMock(side_effect=_must_not_be_called)
    monkeypatch.setattr("worthless.proxy.app.reconstruct_key", reconstruct_mock)
    monkeypatch.setattr("worthless.proxy.app.reconstruct_key_fp", reconstruct_fp_mock)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/{alias}/v1/chat/completions",
            headers={"Authorization": f"Bearer {shard_a}"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 402
    assert reconstruct_mock.await_count == 0
    assert reconstruct_fp_mock.await_count == 0


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


def test_proxy_settings_reads_redis_url_env(monkeypatch):
    monkeypatch.setenv("WORTHLESS_REDIS_URL", "redis://cache.local:6379/1")
    settings = ProxySettings(fernet_key=bytearray(b"x" * 44))
    assert settings.redis_url == "redis://cache.local:6379/1"


def test_proxy_settings_redis_url_defaults_to_none(monkeypatch):
    monkeypatch.delenv("WORTHLESS_REDIS_URL", raising=False)
    settings = ProxySettings(fernet_key=bytearray(b"x" * 44))
    assert settings.redis_url is None


@pytest.mark.asyncio
async def test_create_redis_client_rejects_disallowed_scheme():
    """A compromised env can't redirect the counter to unix:// or file://."""
    from worthless.proxy import metering

    with pytest.raises(ValueError, match="scheme"):
        await metering.create_redis_client("unix:///var/run/docker.sock")
    with pytest.raises(ValueError, match="scheme"):
        await metering.create_redis_client("file:///etc/passwd")


@pytest.mark.asyncio
async def test_create_redis_client_missing_package_has_clear_error(monkeypatch):
    """If the scheme is fine but redis isn't installed, error must be actionable."""
    import builtins

    from worthless.proxy import metering

    real_import = builtins.__import__

    def blocking_import(name: str, *args: Any, **kwargs: Any):
        if name == "redis.asyncio":
            raise ImportError("No module named 'redis'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocking_import)

    with pytest.raises(ImportError, match=r"worthless\[redis\]"):
        await metering.create_redis_client("redis://localhost:6379/0")
