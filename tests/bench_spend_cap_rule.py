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

import aiosqlite
import pytest

from worthless.proxy.metering import incr_spend_hot
from worthless.proxy.rules import SpendCapRule
from worthless.storage.schema import SCHEMA

fakeredis = pytest.importorskip("fakeredis")
from fakeredis.aioredis import FakeRedis  # noqa: E402


LEDGER_SIZES = [10, 100, 1_000, 10_000]
CONCURRENCY = 100


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
