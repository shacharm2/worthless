"""Dynamic tests for the Redis hot-path metering layer.

These tests drive the **real** ``redis.asyncio.Redis`` client rather than the
hand-rolled in-memory stubs in ``test_redis_metering.py``. Two tiers:

* **In-process (always runs).** Uses ``fakeredis.aioredis.FakeRedis`` so the
  actual redis-py client code paths execute — protocol encoding, decoding,
  command parsing, connection pool, error classes. Catches bugs a stub hides
  (e.g. wrong argument types, wrong error class caught, SET NX misuse).

* **Docker-gated (opt-in).** Spins a real Redis container and exercises
  ``create_redis_client`` end-to-end, including PING at startup and the
  lifespan wiring in ``create_app``. Skipped when docker is unavailable
  OR when ``WORTHLESS_TEST_REDIS_URL`` is not set — do not bring up a daemon
  silently in CI.

Invariants under test (independent of backend choice):

* Cache miss + over-cap SQLite ledger → rehydrate + deny.
* Redis transport error → fall back to SQLite path (no 402-storm).
* Malformed counter → rehydrate (not silently 0).
* ``SET NX`` really does not clobber a fresher concurrent writer.
* ``create_redis_client`` calls ``PING`` against a real server.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from typing import Any

import aiosqlite
import pytest

from worthless.proxy.errors import spend_cap_error_response
from worthless.proxy.metering import (
    RedisValueError,
    create_redis_client,
    get_spend_hot,
    incr_spend_hot,
    record_spend,
    rehydrate_spend_hot,
    spend_key,
)
from worthless.proxy.rules import SpendCapRule
from worthless.storage.schema import SCHEMA


# ---------------------------------------------------------------------------
# Module-level availability guards
# ---------------------------------------------------------------------------

fakeredis = pytest.importorskip("fakeredis")
from fakeredis.aioredis import FakeRedis  # noqa: E402

try:
    from redis.exceptions import ConnectionError as RedisConnectionError
except ImportError:  # pragma: no cover — redis is a test dep, must be installed
    RedisConnectionError = ConnectionError  # type: ignore[misc,assignment]


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(  # noqa: S607 — PATH-resolved docker after shutil.which check
            ["docker", "info"], capture_output=True, timeout=5, check=False
        )
        return r.returncode == 0
    except Exception:
        return False


docker_required = pytest.mark.skipif(
    not _docker_available() and not os.environ.get("WORTHLESS_TEST_REDIS_URL"),
    reason="docker not available and WORTHLESS_TEST_REDIS_URL not set",
)


# ---------------------------------------------------------------------------
# SQLite fixture
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


async def _configure_cap(db: aiosqlite.Connection, alias: str, cap: float) -> None:
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
# fakeredis fixture — a real redis.asyncio.Redis wired to an in-process server
# ---------------------------------------------------------------------------


@pytest.fixture
async def fake_redis():
    r = FakeRedis(decode_responses=False)
    try:
        yield r
    finally:
        await r.aclose()


@pytest.fixture
async def fake_redis_seeded(fake_redis):
    """Isolates each test by using a unique alias prefix."""
    prefix = f"test-{uuid.uuid4().hex[:8]}"
    return fake_redis, prefix


# ===========================================================================
# TIER 1: fakeredis — real client, in-process server
# ===========================================================================


@pytest.mark.asyncio
async def test_fakeredis_incrby_roundtrips_through_real_client(fake_redis):
    """Sanity check the fake server speaks the real protocol."""
    assert await incr_spend_hot(fake_redis, "alice", 100) == 100
    assert await incr_spend_hot(fake_redis, "alice", 50) == 150
    assert await get_spend_hot(fake_redis, "alice") == 150


@pytest.mark.asyncio
async def test_fakeredis_set_nx_does_not_clobber(fake_redis):
    """SET NX semantics against the real protocol — NOT a stub assertion."""
    # Pre-seed a "fresher" value via INCR.
    await incr_spend_hot(fake_redis, "alice", 300)
    # A warmer that uses SET NX must be a no-op here.
    await fake_redis.set(spend_key("alice"), 100, nx=True)
    assert await get_spend_hot(fake_redis, "alice") == 300


@pytest.mark.asyncio
async def test_fakeredis_missing_key_is_none(fake_redis):
    assert await get_spend_hot(fake_redis, "nobody") is None


@pytest.mark.asyncio
async def test_fakeredis_malformed_raises_redis_value_error(fake_redis):
    """Plant a non-integer via the real client and assert the wrapper raises."""
    await fake_redis.set(spend_key("alice"), b"tampered")
    with pytest.raises(RedisValueError):
        await get_spend_hot(fake_redis, "alice")


@pytest.mark.asyncio
async def test_fakeredis_rehydrate_sets_nx_and_returns_sqlite_total(sqlite_db, fake_redis):
    db, db_path = sqlite_db
    await _record_tokens(db_path, "alice", 500)

    total = await rehydrate_spend_hot(fake_redis, db, "alice")
    assert total == 500
    # Real GET against the fake server confirms the warm landed.
    assert await get_spend_hot(fake_redis, "alice") == 500


@pytest.mark.asyncio
async def test_fakeredis_rehydrate_nx_respects_concurrent_writer(sqlite_db, fake_redis):
    db, db_path = sqlite_db
    await _record_tokens(db_path, "alice", 100)
    # Simulate a concurrent request that already INCR'd past the SQLite total.
    await incr_spend_hot(fake_redis, "alice", 400)

    total = await rehydrate_spend_hot(fake_redis, db, "alice")
    # rehydrate reports the SQLite SUM (100) but the NX guard kept 400.
    assert total == 100
    assert await get_spend_hot(fake_redis, "alice") == 400


# -- SpendCapRule against fakeredis ------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_rule_fakeredis_under_cap_allows(sqlite_db, fake_redis):
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await incr_spend_hot(fake_redis, "alice", 500)

    rule = SpendCapRule(db=db, redis=fake_redis)
    assert await rule.evaluate("alice", object(), provider="openai", body=b"") is None


@pytest.mark.asyncio
async def test_spend_cap_rule_fakeredis_over_cap_denies(sqlite_db, fake_redis):
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await incr_spend_hot(fake_redis, "alice", 1500)

    rule = SpendCapRule(db=db, redis=fake_redis)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == spend_cap_error_response(provider="openai").status_code


@pytest.mark.asyncio
async def test_spend_cap_rule_fakeredis_miss_rehydrates_and_denies(sqlite_db, fake_redis):
    """Cold-start scenario driven by the real client: Redis empty, SQLite hot."""
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 2_000)  # over cap in ledger

    rule = SpendCapRule(db=db, redis=fake_redis)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402
    # After rehydrate, a subsequent real GET returns the warmed counter.
    assert await get_spend_hot(fake_redis, "alice") == 2_000


@pytest.mark.asyncio
async def test_spend_cap_rule_fakeredis_malformed_rehydrates(sqlite_db, fake_redis):
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 5_000)
    # Plant a bogus value via the real client.
    await fake_redis.set(spend_key("alice"), b"not-a-number")

    rule = SpendCapRule(db=db, redis=fake_redis)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402


@pytest.mark.asyncio
async def test_spend_cap_rule_fakeredis_closed_connection_falls_back(sqlite_db, fake_redis):
    """Close the real client mid-flight; rule must fall back to SQLite, not 402."""
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 300)  # under cap

    await fake_redis.aclose()  # every future command on this client raises

    rule = SpendCapRule(db=db, redis=fake_redis)
    # Under-cap SQLite → allow, even though Redis is gone. No 402-storm.
    assert await rule.evaluate("alice", object(), provider="openai", body=b"") is None


@pytest.mark.asyncio
async def test_spend_cap_rule_fakeredis_closed_connection_denies_over_cap_via_sqlite(
    sqlite_db, fake_redis
):
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 9_000)

    await fake_redis.aclose()

    rule = SpendCapRule(db=db, redis=fake_redis)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402


# -- record_spend against fakeredis ------------------------------------------


@pytest.mark.asyncio
async def test_record_spend_fakeredis_dual_phase(sqlite_db, fake_redis):
    _, db_path = sqlite_db
    await record_spend(db_path, "alice", 42, "gpt-4o", "openai", redis=fake_redis)

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT tokens FROM spend_log WHERE key_alias = ?", ("alice",)
        ) as cur:
            row = await cur.fetchone()
    assert row == (42,)
    assert await get_spend_hot(fake_redis, "alice") == 42


@pytest.mark.asyncio
async def test_record_spend_fakeredis_concurrent_incrs_no_lost_updates(sqlite_db, fake_redis):
    """Real INCRBY is atomic — drive N concurrent record_spend calls and
    assert the counter equals the sum. Exercises the real client's async
    pipeline, not a serialised stub."""
    _, db_path = sqlite_db
    amounts = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

    await asyncio.gather(
        *[record_spend(db_path, "alice", n, None, "openai", redis=fake_redis) for n in amounts]
    )

    assert await get_spend_hot(fake_redis, "alice") == sum(amounts)
    # SQLite sees every insert too (authoritative).
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
            ("alice",),
        ) as cur:
            count, total = await cur.fetchone()  # type: ignore[misc]
    assert count == len(amounts)
    assert total == sum(amounts)


# -- create_redis_client against fakeredis via Redis.from_url monkeypatch ----


@pytest.mark.asyncio
async def test_create_redis_client_calls_ping_via_real_client(monkeypatch):
    """Intercept Redis.from_url to return a FakeRedis; assert ping() ran."""
    from redis.asyncio import Redis

    captured: dict[str, Any] = {}

    def fake_from_url(url: str, **kwargs: Any):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeRedis(decode_responses=kwargs.get("decode_responses", False))

    monkeypatch.setattr(Redis, "from_url", staticmethod(fake_from_url))

    client = await create_redis_client("redis://localhost:6379/0")
    try:
        # Timeouts are plumbed through.
        assert captured["kwargs"]["socket_timeout"] == 2.0
        assert captured["kwargs"]["socket_connect_timeout"] == 1.0
        assert captured["kwargs"]["health_check_interval"] == 30
        # PING must have succeeded — use the returned client to confirm it's live.
        assert await client.ping() is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_create_redis_client_ping_failure_propagates(monkeypatch):
    """If the real client's PING fails at boot, the caller sees the error
    at startup — not on the first request."""
    from redis.asyncio import Redis

    class _PingFailingRedis(FakeRedis):
        async def ping(self):
            raise RedisConnectionError("simulated unreachable")

    def fake_from_url(url: str, **kwargs: Any):
        return _PingFailingRedis(decode_responses=kwargs.get("decode_responses", False))

    monkeypatch.setattr(Redis, "from_url", staticmethod(fake_from_url))

    with pytest.raises(RedisConnectionError, match="simulated unreachable"):
        await create_redis_client("redis://localhost:6379/0")


# ===========================================================================
# TIER 2: real Redis container (docker-gated)
# ===========================================================================


@pytest.fixture(scope="session")
def real_redis_url():
    """Return a URL to a real Redis daemon. Session-scoped: one container
    per test session, not per test.

    Order of preference:
    1. ``WORTHLESS_TEST_REDIS_URL`` env var — useful in CI or when the
       developer already has Redis running.
    2. Spin a ``redis:7-alpine`` container via ``docker run``.
    """
    pre_existing = os.environ.get("WORTHLESS_TEST_REDIS_URL")
    if pre_existing:
        yield pre_existing
        return

    if not shutil.which("docker"):
        pytest.skip("docker not available and WORTHLESS_TEST_REDIS_URL unset")

    name = f"worthless-test-redis-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(  # noqa: S603, S607 — test-only, docker resolved via shutil.which above
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-p",
            "127.0.0.1:0:6379",
            "redis:7-alpine",
            "redis-server",
            "--save",
            "",
            "--appendonly",
            "no",
            "--maxmemory",
            "32mb",
            "--maxmemory-policy",
            "noeviction",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if run.returncode != 0:
        pytest.skip(f"docker run failed: {run.stderr.strip()}")

    try:
        port_proc = subprocess.run(  # noqa: S603, S607 — test-only
            ["docker", "port", name, "6379/tcp"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        host_port = port_proc.stdout.strip().split(":")[-1]
        url = f"redis://127.0.0.1:{host_port}/0"

        # Poll until PING works, up to ~5s. Use a synchronous check because
        # this fixture is session-scoped and can't be async.
        import time as _time

        deadline = _time.time() + 5.0
        last_err: str = ""
        ready = False
        while _time.time() < deadline:
            probe = subprocess.run(  # noqa: S603, S607 — test-only
                ["docker", "exec", name, "redis-cli", "ping"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if probe.returncode == 0 and "PONG" in probe.stdout:
                ready = True
                break
            last_err = probe.stderr or probe.stdout
            _time.sleep(0.2)

        if not ready:
            subprocess.run(  # noqa: S603, S607 — test-only
                ["docker", "stop", name], capture_output=True, timeout=15, check=False
            )
            pytest.skip(f"real redis did not become ready in 5s: {last_err}")

        yield url
    finally:
        subprocess.run(  # noqa: S603, S607 — test-only
            ["docker", "stop", name],
            capture_output=True,
            timeout=15,
            check=False,
        )


@pytest.fixture
async def real_redis_client(real_redis_url):
    """Fresh client + alias isolation. Flushes keys for the test alias
    before and after to keep tests independent without sharing state
    through the session-scoped container."""
    client = await create_redis_client(real_redis_url)
    try:
        await client.delete(spend_key("alice"))
        yield client
    finally:
        try:
            await client.delete(spend_key("alice"))
        except Exception:
            pass
        await client.aclose()


@pytest.mark.asyncio
@docker_required
async def test_real_redis_create_client_pings_at_startup(real_redis_url):
    """End-to-end: ``create_redis_client`` against a real Redis container."""
    client = await create_redis_client(real_redis_url)
    try:
        assert await client.ping() is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
@docker_required
async def test_real_redis_rule_denies_over_cap(real_redis_client, sqlite_db):
    """Full stack: SpendCapRule + real redis-py + real TCP + real Redis."""
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await incr_spend_hot(real_redis_client, "alice", 2_000)

    rule = SpendCapRule(db=db, redis=real_redis_client)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402


@pytest.mark.asyncio
@docker_required
async def test_real_redis_rule_miss_rehydrates_from_sqlite(real_redis_client, sqlite_db):
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 2_500)  # over cap in ledger

    rule = SpendCapRule(db=db, redis=real_redis_client)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None
    assert result.status_code == 402
    # Real GET against real Redis confirms warm-up landed.
    raw = await real_redis_client.get(spend_key("alice"))
    assert raw is not None
    assert int(raw) == 2_500


@pytest.mark.asyncio
@docker_required
async def test_real_redis_record_spend_dual_phase_end_to_end(real_redis_client, sqlite_db):
    _, db_path = sqlite_db

    await record_spend(db_path, "alice", 77, "gpt-4o", "openai", redis=real_redis_client)

    raw = await real_redis_client.get(spend_key("alice"))
    assert raw is not None
    assert int(raw) == 77


@pytest.mark.asyncio
@docker_required
async def test_real_redis_noeviction_policy_is_set(real_redis_client):
    """Guard against the compose-file regression: the test container is
    launched with noeviction to match production and to prove the operational
    contract. If someone flips this to allkeys-lru in prod, this test stays
    green (it probes the test container) but the hardening logic is still
    correct. Keep this as a live check of our assumed policy."""
    policy = await real_redis_client.config_get("maxmemory-policy")
    # config_get returns {b"maxmemory-policy": b"noeviction"} when decode_responses=False
    value = policy.get(b"maxmemory-policy") or policy.get("maxmemory-policy")
    if isinstance(value, bytes):
        value = value.decode()
    assert value == "noeviction"
