"""Benchmark SpendCapRule — SQLite path vs Redis hot path (worthless-n48x).

Decision we're trying to inform: at v1.1's self-hosted single-node RPS
target, does SQLite already handle the gate path fast enough that Redis is
a speculative optimisation?

Measures three things across four ledger sizes (10 / 100 / 1k / 10k rows):

1. Gate latency per ``SpendCapRule.evaluate`` call — single-request.
2. Gate latency under concurrent load (100 parallel evaluates via gather).
3. Sanity: the Redis path stays roughly flat vs ledger size.

Uses fakeredis (real ``redis.asyncio.Redis`` client, in-process server) so
the result reflects real redis-py overhead minus the network hop. A TCP
Redis would add ~0.1-0.5ms of loopback latency on top of what we measure.

Run locally:

    pytest tests/bench_spend_cap_rule.py \\
        -p no:xdist -p no:randomly -o addopts= \\
        --benchmark-only --benchmark-columns=min,median,mean,p95,p99,ops

Key columns: ``median`` (p50), ``p95``, ``p99``, ``ops`` (calls/sec).

The gate runs BEFORE XOR reconstruction, so ``ops`` is the theoretical
per-alias request ceiling on a single event loop.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid

import aiosqlite
import pytest

from worthless.proxy.metering import create_redis_client, incr_spend_hot, spend_key
from worthless.proxy.rules import SpendCapRule
from worthless.storage.schema import SCHEMA

fakeredis = pytest.importorskip("fakeredis")
from fakeredis.aioredis import FakeRedis  # noqa: E402


LEDGER_SIZES = [10, 100, 1_000, 10_000]
CONCURRENCY = 100


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        return r.returncode == 0
    except Exception:
        return False


docker_required = pytest.mark.skipif(
    not _docker_available() and not os.environ.get("WORTHLESS_TEST_REDIS_URL"),
    reason="docker not available and WORTHLESS_TEST_REDIS_URL not set",
)


async def _seed_ledger(db_path: str, alias: str, rows: int) -> None:
    """Populate ``rows`` spend_log entries for the given alias."""
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            [(alias, 1, None, "openai") for _ in range(rows)],
        )
        await db.commit()


@pytest.fixture(params=LEDGER_SIZES, ids=lambda n: f"ledger={n}")
async def seeded(tmp_path, request):
    """Yield ``(rule_sqlite, rule_redis, alias, cleanup_list)`` with N rows seeded."""
    db_path = tmp_path / "worthless.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            ("alice", 1_000_000_000.0),  # generous cap so we always pass
        )
        await db.commit()
    await _seed_ledger(str(db_path), "alice", request.param)

    sqlite_db = await aiosqlite.connect(db_path)
    await sqlite_db.execute("PRAGMA journal_mode=WAL")
    await sqlite_db.execute("PRAGMA busy_timeout=5000")

    redis_db = await aiosqlite.connect(db_path)
    await redis_db.execute("PRAGMA journal_mode=WAL")
    await redis_db.execute("PRAGMA busy_timeout=5000")

    redis = FakeRedis(decode_responses=False)
    # Warm the Redis counter to match the SQLite total (post-rehydrate state).
    await incr_spend_hot(redis, "alice", request.param)

    rule_sqlite = SpendCapRule(db=sqlite_db, redis=None)
    rule_redis = SpendCapRule(db=redis_db, redis=redis)

    yield rule_sqlite, rule_redis, "alice", request.param

    await sqlite_db.close()
    await redis_db.close()
    await redis.aclose()


# ---------------------------------------------------------------------------
# Single-request latency
# ---------------------------------------------------------------------------


def _run_async(coro_factory):
    """pytest-benchmark adapter — wrap an async callable for sync benchmarking.

    We explicitly reuse one event loop per benchmark so fixture/loop
    overhead doesn't bias the timing.
    """
    loop = asyncio.new_event_loop()
    try:
        return lambda: loop.run_until_complete(coro_factory())
    finally:
        pass  # loop closed by the caller after all iterations


@pytest.mark.benchmark(group="single-sqlite")
def test_bench_single_sqlite(benchmark, seeded):
    rule_sqlite, _rule_redis, alias, rows = seeded

    async def one():
        body = b'{"model":"gpt-4","max_tokens":100}'
        await rule_sqlite.evaluate(alias, object(), provider="openai", body=body)
        # Release immediately so the reservation doesn't pile up across iterations.
        await rule_sqlite.release_reservation(alias, 100)

    loop = asyncio.new_event_loop()
    try:
        benchmark.extra_info["rows"] = rows
        benchmark.extra_info["backend"] = "sqlite"
        benchmark(lambda: loop.run_until_complete(one()))
    finally:
        loop.close()


@pytest.mark.benchmark(group="single-redis")
def test_bench_single_redis(benchmark, seeded):
    _rule_sqlite, rule_redis, alias, rows = seeded

    async def one():
        body = b'{"model":"gpt-4","max_tokens":100}'
        await rule_redis.evaluate(alias, object(), provider="openai", body=body)
        await rule_redis.release_reservation(alias, 100)

    loop = asyncio.new_event_loop()
    try:
        benchmark.extra_info["rows"] = rows
        benchmark.extra_info["backend"] = "redis"
        benchmark(lambda: loop.run_until_complete(one()))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Concurrent load (100 parallel evaluates via asyncio.gather)
#
# Stresses the reservation lock. SQLite path has BEGIN IMMEDIATE contention;
# Redis path shares only the in-memory lock + GET round-trip.
# ---------------------------------------------------------------------------


@pytest.mark.benchmark(group="concurrent-sqlite")
def test_bench_concurrent_sqlite(benchmark, seeded):
    rule_sqlite, _rule_redis, alias, rows = seeded

    async def many():
        body = b'{"model":"gpt-4","max_tokens":100}'
        await asyncio.gather(
            *[
                rule_sqlite.evaluate(alias, object(), provider="openai", body=body)
                for _ in range(CONCURRENCY)
            ]
        )
        # Bulk release after the wave.
        await rule_sqlite.release_reservation(alias, 100 * CONCURRENCY)

    loop = asyncio.new_event_loop()
    try:
        benchmark.extra_info["rows"] = rows
        benchmark.extra_info["concurrency"] = CONCURRENCY
        benchmark.extra_info["backend"] = "sqlite"
        benchmark(lambda: loop.run_until_complete(many()))
    finally:
        loop.close()


@pytest.mark.benchmark(group="concurrent-redis")
def test_bench_concurrent_redis(benchmark, seeded):
    _rule_sqlite, rule_redis, alias, rows = seeded

    async def many():
        body = b'{"model":"gpt-4","max_tokens":100}'
        await asyncio.gather(
            *[
                rule_redis.evaluate(alias, object(), provider="openai", body=body)
                for _ in range(CONCURRENCY)
            ]
        )
        await rule_redis.release_reservation(alias, 100 * CONCURRENCY)

    loop = asyncio.new_event_loop()
    try:
        benchmark.extra_info["rows"] = rows
        benchmark.extra_info["concurrency"] = CONCURRENCY
        benchmark.extra_info["backend"] = "redis"
        benchmark(lambda: loop.run_until_complete(many()))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# TCP Redis variant — fakeredis is in-process, so it omits the loopback
# round-trip a real Redis would pay. This variant spins redis:7-alpine via
# docker, uses the real create_redis_client (timeouts + PING), and repeats
# the single/concurrent benchmarks so the writeup can quote both numbers.
#
# Gated by docker availability; env override WORTHLESS_TEST_REDIS_URL lets
# CI or a pre-running local Redis substitute the container.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tcp_redis_url():
    pre_existing = os.environ.get("WORTHLESS_TEST_REDIS_URL")
    if pre_existing:
        yield pre_existing
        return
    if not shutil.which("docker"):
        pytest.skip("docker not available and WORTHLESS_TEST_REDIS_URL unset")

    name = f"worthless-bench-redis-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(  # noqa: S603, S607 — test-only
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
            "128mb",
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

        import time as _time

        deadline = _time.time() + 5.0
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
            _time.sleep(0.2)
        if not ready:
            subprocess.run(  # noqa: S603, S607 — test-only
                ["docker", "stop", name],
                capture_output=True,
                timeout=15,
                check=False,
            )
            pytest.skip("real redis did not become ready in 5s")
        yield url
    finally:
        subprocess.run(  # noqa: S603, S607 — test-only
            ["docker", "stop", name],
            capture_output=True,
            timeout=15,
            check=False,
        )


@pytest.fixture(params=LEDGER_SIZES, ids=lambda n: f"ledger={n}")
async def seeded_tcp(tmp_path, request, tcp_redis_url):
    """Same seed pattern as `seeded`, but wires a real TCP Redis client."""
    db_path = tmp_path / "worthless.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            ("alice", 1_000_000_000.0),
        )
        await db.commit()
    await _seed_ledger(str(db_path), "alice", request.param)

    redis_db = await aiosqlite.connect(db_path)
    await redis_db.execute("PRAGMA journal_mode=WAL")
    await redis_db.execute("PRAGMA busy_timeout=5000")

    redis = await create_redis_client(tcp_redis_url)
    # Alias-scope isolation across benchmark params: delete then seed.
    await redis.delete(spend_key("alice"))
    await incr_spend_hot(redis, "alice", request.param)

    rule_tcp = SpendCapRule(db=redis_db, redis=redis)

    yield rule_tcp, "alice", request.param

    # Best-effort cleanup. The benchmark body runs each iteration on its own
    # event loop (see _run_async), so by the time teardown runs the redis
    # client's bound loop may be closed. Swallow the RuntimeError and rely
    # on docker container teardown for true cleanup.
    for cleanup in (
        lambda: redis.delete(spend_key("alice")),
        lambda: redis.aclose(),
        lambda: redis_db.close(),
    ):
        try:
            await cleanup()
        except Exception:
            pass


@pytest.mark.benchmark(group="single-redis-tcp")
@docker_required
def test_bench_single_redis_tcp(benchmark, seeded_tcp):
    rule, alias, rows = seeded_tcp

    async def one():
        body = b'{"model":"gpt-4","max_tokens":100}'
        await rule.evaluate(alias, object(), provider="openai", body=body)
        await rule.release_reservation(alias, 100)

    loop = asyncio.new_event_loop()
    try:
        benchmark.extra_info["rows"] = rows
        benchmark.extra_info["backend"] = "redis-tcp"
        benchmark(lambda: loop.run_until_complete(one()))
    finally:
        loop.close()


@pytest.mark.benchmark(group="concurrent-redis-tcp")
@docker_required
def test_bench_concurrent_redis_tcp(benchmark, seeded_tcp):
    rule, alias, rows = seeded_tcp

    async def many():
        body = b'{"model":"gpt-4","max_tokens":100}'
        await asyncio.gather(
            *[
                rule.evaluate(alias, object(), provider="openai", body=body)
                for _ in range(CONCURRENCY)
            ]
        )
        await rule.release_reservation(alias, 100 * CONCURRENCY)

    loop = asyncio.new_event_loop()
    try:
        benchmark.extra_info["rows"] = rows
        benchmark.extra_info["concurrency"] = CONCURRENCY
        benchmark.extra_info["backend"] = "redis-tcp"
        benchmark(lambda: loop.run_until_complete(many()))
    finally:
        loop.close()
