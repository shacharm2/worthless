"""Failure-mode regression tests for the Redis hot-path metering feature.

Complements ``tests/test_redis_metering.py`` (which covers the happy paths,
cache semantics, reservation mechanism, and SR-03 invariant in the full
pipeline) with explicit guards for failure modes surfaced during the
earlier resilience review that previously had no regression test:

* Slow / hung Redis (not errored) — timeout-class exception must fall back
  to SQLite, not hang the request handler.
* Restart-wipe (FLUSHDB mid-stream) — cache miss on a key that was
  previously populated must rehydrate from SQLite and still honour the cap.
* Partial-write drift — when ``record_spend`` succeeded on SQLite but the
  Redis ``INCR`` failed (swallowed), the NEXT evaluate() on the same alias
  must not silently let more spend through just because the counter lags
  SQLite. Currently does (documented with ``xfail(strict=True)``).
* Eviction mid-stream — a key that had a value and now returns nil
  (defense-in-depth vs ``noeviction``) still rehydrates correctly.
* Reservation leak on adapter error — repeated evaluate-then-release
  cycles keep ``_reserved`` bounded at 0.
* Structural rule-ordering test — ``RulesEngine.release_spend_reservation``
  drains reservations on EVERY reservation-holding rule regardless of
  position in the chain, so future rule reorders do not leak.
* Lifespan shutdown — a pending ``record_spend`` whose Redis client has
  already been closed still writes to SQLite (ledger authoritative).
* Memory pressure — ``_reserved`` grows unboundedly with unique aliases
  (documented with ``xfail(strict=True)``; bounded LRU is a TODO).

Redis fakes reuse the interface from ``tests/test_redis_metering.py``
(``_FakeRedis``, ``_ExplodingRedis``). Extensions (``_SlowRedis``,
``_FlushingRedis``, ``_DriftingRedis``) subclass or compose — they never
reinvent state.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiosqlite
import pytest

from worthless.proxy.errors import (
    ErrorResponse,
)
from worthless.proxy.metering import (
    SpendDirtyTracker,
    get_spend_hot,
    incr_spend_hot,
    record_spend,
    spend_key,
    sum_spend_sqlite,
)
from worthless.proxy.rules import RulesEngine, SpendCapRule, TokenBudgetRule
from worthless.storage.schema import SCHEMA


# ---------------------------------------------------------------------------
# Minimal Redis fakes. Share the spirit of tests/test_redis_metering.py but
# kept here so the file is self-contained.
# ---------------------------------------------------------------------------


class _FakeRedis:
    """In-memory Redis stub. Mirrors test_redis_metering._FakeRedis."""

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

    # Test helpers — not part of the real Redis API, used by the FLUSHDB /
    # eviction simulations.
    def flushdb(self) -> None:
        self.store.clear()

    def evict(self, key: str) -> None:
        self.store.pop(key, None)


class _SlowRedis(_FakeRedis):
    """Accepts connections but ``GET`` raises TimeoutError after a tiny pause.

    Models the production behaviour where redis-py has
    ``socket_timeout=2.0`` and a hung server triggers
    ``redis.exceptions.TimeoutError`` — a subclass of ``OSError`` /
    ``asyncio.TimeoutError`` depending on version. We just raise
    ``asyncio.TimeoutError`` here; SpendCapRule's handler is
    ``except Exception`` so both map to the same fallback path.
    """

    async def get(self, key: str) -> bytes | None:  # noqa: ARG002
        # tiny real sleep so the test still exercises the async scheduler
        await asyncio.sleep(0.01)
        raise TimeoutError("simulated redis socket_timeout")


class _DriftingRedis(_FakeRedis):
    """GET returns a stale value, and we can assert no silent rehydrate.

    Used to prove the drift bug: when the committed total in SQLite is
    higher than the Redis counter (because a previous INCR failed and was
    swallowed by ``record_spend``), the gate silently uses the low Redis
    value — letting spend through. Contrast with the cache-miss path,
    which DOES rehydrate.
    """


# ---------------------------------------------------------------------------
# Shared fixtures — a real aiosqlite connection with SCHEMA applied.
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
# Failure mode 1: slow / hung Redis.
#
# Contract: SpendCapRule must not hang on a Redis GET that exceeds the
# socket_timeout. The redis-py client raises a timeout-class exception at
# the socket_timeout boundary; _evaluate_redis catches any non-RedisValueError
# Exception and falls back to the SQLite path. We simulate with
# _SlowRedis.get raising TimeoutError and assert the evaluate() completes
# in bounded wall-clock time via asyncio.wait_for.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_rule_slow_redis_times_out_bounded(sqlite_db):
    """Hung Redis → fallback to SQLite within a bounded wall-clock budget.

    This guards the fix that added ``socket_timeout=2.0`` / ``socket_connect_timeout=1.0``
    in ``create_redis_client``. The stub raises TimeoutError immediately on
    GET; the rule must catch and drop to SQLite (not re-raise, not hang).
    """
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 200)  # under cap via SQLite

    rule = SpendCapRule(db=db, redis=_SlowRedis())

    # 3.0s is generous vs the production 2.0s socket_timeout budget and
    # well above the stub's own 10ms pause.
    result = await asyncio.wait_for(
        rule.evaluate("alice", object(), provider="openai", body=b""),
        timeout=3.0,
    )
    assert result is None, "Slow Redis must fall back to SQLite, not deny a legitimate request"


@pytest.mark.asyncio
async def test_spend_cap_rule_slow_redis_still_denies_over_cap(sqlite_db):
    """Bounded fallback path stays fail-closed when SQLite says over cap."""
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 5_000)

    rule = SpendCapRule(db=db, redis=_SlowRedis())

    result = await asyncio.wait_for(
        rule.evaluate("alice", object(), provider="openai", body=b""),
        timeout=3.0,
    )
    assert isinstance(result, ErrorResponse)
    assert result.status_code == 402


# ---------------------------------------------------------------------------
# Failure mode 2: Redis restart / FLUSHDB wipes counters.
#
# Mostly covered by the existing miss-rehydrate test. This one exercises
# the transition: seed counter, THEN flush, then evaluate — proves the
# mid-stream wipe path, not only the cold-start path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_rule_survives_flushdb_mid_stream(sqlite_db):
    """Redis FLUSHDB between requests must rehydrate from SQLite and honour cap."""
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 2_000)  # over cap in ledger

    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 2_000)  # counter matches ledger

    rule = SpendCapRule(db=db, redis=r)

    # Warm state: over cap.
    result_warm = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result_warm is not None and result_warm.status_code == 402

    # Simulate a Redis restart without persistence (or a manual FLUSHDB).
    r.flushdb()

    # Next evaluate() sees the counter as missing → rehydrate from SQLite
    # → still over cap → deny. Without the rehydrate fix this would see 0
    # and silently allow the request.
    result_after = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result_after is not None, "FLUSHDB mid-stream must not reset the cap"
    assert result_after.status_code == 402
    # And the counter is warm again for the next request.
    assert r.store.get(spend_key("alice")) == b"2000"


# ---------------------------------------------------------------------------
# Failure mode 3: partial-write drift — record_spend succeeded on SQLite
# but the Redis INCR failed (swallowed). The counter lags SQLite.
#
# Current behaviour of _evaluate_redis: if the GET returns a real int, it
# is trusted verbatim. There is NO cross-check against SQLite SUM — only
# cache *miss* triggers rehydrate. So a drifted-low counter silently under-
# reports committed spend and lets more requests through.
#
# Documented with xfail(strict=True) because a regression test must document
# the bug until the fix ships. Track in beads: "metering: detect Redis
# counter drift vs SQLite SUM".
# ---------------------------------------------------------------------------


class _IncrFailingRedis(_FakeRedis):
    """GET/SET succeed, INCRBY always fails — simulates Redis mid-degradation.

    Real-world trigger: Redis reachable for the gate's GET, unreachable
    for record_spend's INCR (e.g. transient packet loss that happened to
    land during the background task's write). The SQLite INSERT commits,
    the INCR is swallowed, and the counter is stuck lagging the ledger.
    """

    async def incrby(self, key: str, amount: int) -> int:  # noqa: ARG002
        raise ConnectionError("simulated INCRBY failure")


@pytest.mark.asyncio
async def test_spend_cap_rule_detects_partial_write_drift(sqlite_db):
    """worthless-woh7: record_spend marks the alias dirty on INCR failure,
    the next evaluate forces a rehydrate from SQLite, the stale counter
    is overwritten, and the cap is honoured.

    Drives the fix through its real trigger (record_spend under a failing
    Redis) rather than planting a bad counter directly.
    """
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 500)  # first 500 via normal flow

    tracker = SpendDirtyTracker()
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 500)  # Redis and SQLite agree at 500

    # record_spend now needs to add another 500 tokens, but Redis's INCR
    # fails. The SQLite INSERT commits, the INCR is swallowed, and the
    # tracker records the drift. No exception escapes.
    failing = _IncrFailingRedis()
    await record_spend(
        db_path,
        "alice",
        500,
        None,
        "openai",
        redis=failing,
        dirty_tracker=tracker,
    )

    # State after the failure: SQLite = 1000 (at cap), Redis = 500 (stale).
    assert await sum_spend_sqlite(db, "alice") == 1000
    assert await get_spend_hot(r, "alice") == 500, "drift: Redis lags SQLite"
    assert await tracker.is_dirty("alice"), "INCR failure must mark alias dirty"

    # Now the gate runs. Without drift detection, the stale 500 counter
    # plus a max_tokens=1 reservation passes (500 + 1 < 1000 = false since
    # committed is already at 1000, but the gate only sees 500). With
    # drift detection, the tracker flag forces rehydrate to 1000, and the
    # request is denied.
    rule = SpendCapRule(db=db, redis=r, dirty_tracker=tracker)
    body = b'{"model":"gpt-4","max_tokens":1}'
    result = await rule.evaluate("alice", object(), provider="openai", body=body)
    assert result is not None and result.status_code == 402

    # Side effects: counter healed, tracker cleared.
    assert await get_spend_hot(r, "alice") == 1000
    assert not await tracker.is_dirty("alice")

    # A follow-up request (no dirty flag) still denies via the normal path.
    result2 = await rule.evaluate("alice", object(), provider="openai", body=body)
    assert result2 is not None and result2.status_code == 402


@pytest.mark.asyncio
async def test_spend_cap_rule_without_dirty_tracker_still_works(sqlite_db):
    """Backward compatibility: SpendCapRule without a dirty_tracker uses the
    old GET-or-rehydrate path. No-regression guard.
    """
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 500)

    rule = SpendCapRule(db=db, redis=r)  # no dirty_tracker
    body = b'{"model":"gpt-4","max_tokens":10}'
    assert await rule.evaluate("alice", object(), provider="openai", body=body) is None


@pytest.mark.asyncio
async def test_record_spend_without_dirty_tracker_swallows_redis_failure(sqlite_db):
    """record_spend without a dirty_tracker still swallows Redis failures
    (pre-existing behaviour) — the tracker is optional.
    """
    _, db_path = sqlite_db
    failing = _IncrFailingRedis()
    await record_spend(db_path, "alice", 50, None, "openai", redis=failing)
    # SQLite still has the record.
    async with aiosqlite.connect(db_path) as wdb:
        async with wdb.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
            ("alice",),
        ) as cur:
            (total,) = await cur.fetchone()  # type: ignore[misc]
    assert total == 50


# ---------------------------------------------------------------------------
# Failure mode 4: eviction mid-stream.
#
# Even with noeviction in compose, test defense-in-depth: if a key that
# previously had a value returns nil, the rule must rehydrate. This is the
# mid-stream cousin of the cold-start test already in test_redis_metering.py.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_rule_rehydrates_on_mid_stream_eviction(sqlite_db):
    """A previously-present key that goes missing must not read as 0."""
    db, db_path = sqlite_db
    await _configure_cap(db, "alice", 1000.0)
    await _record_tokens(db_path, "alice", 1_500)  # over cap

    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 1_500)

    rule = SpendCapRule(db=db, redis=r)

    # First request: warm counter, over cap, deny.
    first = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert first is not None and first.status_code == 402

    # Simulate an LRU eviction of JUST this key.
    r.evict(spend_key("alice"))

    # Second request: counter missing → rehydrate from SQLite → still deny.
    second = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert second is not None and second.status_code == 402, (
        "Mid-stream eviction must rehydrate from SQLite, not silently reset the cap to 0."
    )


# ---------------------------------------------------------------------------
# Failure mode 5: reservation leak on adapter error.
#
# app.py calls release_spend_reservation on every error path after the
# gate passes (adapter None, decrypt error, reconstruct error, upstream
# timeout, HTTP error, etc.). This test repeatedly drives evaluate +
# release and asserts _reserved[alias] returns to 0 after each cycle —
# so a burst of adapter failures cannot leak reservations and make the
# cap unreachable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reservation_released_on_repeated_adapter_errors(sqlite_db):
    """N evaluate → adapter-fails → release cycles keep _reserved[alias] at 0."""
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 10_000.0)
    r = _FakeRedis()
    rule = SpendCapRule(db=db, redis=r)
    body = b'{"model":"gpt-4","max_tokens":100}'

    for _ in range(25):
        result = await rule.evaluate("alice", object(), provider="openai", body=body)
        assert result is None  # under cap, reserves 100
        # Simulate the app.py error path: release the reservation.
        await rule.release_reservation("alice", 100)

    # Without leak guard, 25 iterations × 100 tokens would accumulate to
    # 2500 reserved and drain a fraction of the cap. With release, we
    # expect back to 0.
    assert rule._reserved.get("alice", 0) == 0, (
        f"Reservation leaked after 25 evaluate/release cycles: "
        f"{rule._reserved.get('alice')} tokens held, expected 0."
    )


@pytest.mark.asyncio
async def test_engine_release_drains_reservation_via_app_api(sqlite_db):
    """End-to-end via RulesEngine.release_spend_reservation (app.py's API)."""
    db, _ = sqlite_db
    await _configure_cap(db, "alice", 10_000.0)
    r = _FakeRedis()
    rule = SpendCapRule(db=db, redis=r)
    engine = RulesEngine(rules=[rule])
    body = b'{"model":"gpt-4","max_tokens":250}'

    for _ in range(10):
        assert await engine.evaluate("alice", object(), provider="openai", body=body) is None
        # This is exactly the call app.py makes on every error path.
        await engine.release_spend_reservation("alice", 250)

    assert rule._reserved.get("alice", 0) == 0


# ---------------------------------------------------------------------------
# Failure mode 6: structural invariant — release_spend_reservation MUST drain
# every reservation-holding rule regardless of position in the chain.
#
# Written against the real RulesEngine and real rule types so a future
# reorder or a new reservation-holding rule inserted between SpendCapRule
# and TokenBudgetRule cannot silently leak reservations. If someone adds
# a NEW rule that holds reservations, this test forces them to teach
# RulesEngine.release_spend_reservation about it (otherwise the leak is
# immediately visible).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_release_drains_both_spend_cap_and_token_budget(sqlite_db):
    """When both reservation-holding rules are in the chain, release drains both."""
    db, _ = sqlite_db
    # Set both a cap and a daily token budget so both rules reserve.
    await db.execute(
        "INSERT INTO enrollment_config (key_alias, spend_cap, token_budget_daily) VALUES (?, ?, ?)",
        ("alice", 10_000.0, 10_000),
    )
    await db.commit()

    r = _FakeRedis()
    spend = SpendCapRule(db=db, redis=r)
    tokens = TokenBudgetRule(db=db)
    # Reverse order deliberately — proves position-independence.
    engine = RulesEngine(rules=[tokens, spend])

    body = b'{"model":"gpt-4","max_tokens":500}'
    assert await engine.evaluate("alice", object(), provider="openai", body=body) is None
    assert spend._reserved.get("alice", 0) > 0
    assert tokens._reserved.get("alice", 0) > 0

    # app.py fires this once on ANY denial or adapter-error path.
    await engine.release_spend_reservation("alice", 500)

    assert spend._reserved.get("alice", 0) == 0, (
        "SpendCapRule reservation leaked after engine.release_spend_reservation"
    )
    assert tokens._reserved.get("alice", 0) == 0, (
        "TokenBudgetRule reservation leaked after engine.release_spend_reservation"
    )


@pytest.mark.asyncio
async def test_engine_release_survives_unknown_rule_in_chain(sqlite_db):
    """A non-reservation-holding rule between the two must not break drain.

    Structural guard: if a future rule is inserted between TokenBudgetRule
    and SpendCapRule, release_spend_reservation must still drain both.
    """
    db, _ = sqlite_db
    await db.execute(
        "INSERT INTO enrollment_config (key_alias, spend_cap, token_budget_daily) VALUES (?, ?, ?)",
        ("alice", 10_000.0, 10_000),
    )
    await db.commit()

    class _NoopRule:
        async def evaluate(
            self, alias, request, *, provider="openai", body=b""
        ) -> ErrorResponse | None:
            return None

    spend = SpendCapRule(db=db, redis=_FakeRedis())
    tokens = TokenBudgetRule(db=db)
    engine = RulesEngine(rules=[tokens, _NoopRule(), spend])

    body = b'{"model":"gpt-4","max_tokens":400}'
    assert await engine.evaluate("alice", object(), provider="openai", body=body) is None

    await engine.release_spend_reservation("alice", 400)

    assert spend._reserved.get("alice", 0) == 0
    assert tokens._reserved.get("alice", 0) == 0


# ---------------------------------------------------------------------------
# Failure mode 8: lifespan shutdown — a pending record_spend with an
# already-closed Redis must still complete the SQLite write.
#
# Models the race where proxy lifespan fires aclose() while a streaming
# BackgroundTask has not yet called record_spend. record_spend MUST NOT
# raise: the SQLite write is authoritative, and a Redis error (from a
# closed client) is swallowed per the existing contract.
# ---------------------------------------------------------------------------


class _ClosedRedis:
    """Simulates a redis.asyncio.Redis instance after aclose() was called."""

    async def get(self, key: str) -> bytes | None:  # noqa: ARG002
        raise ConnectionError("Connection closed")

    async def set(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ARG002
        raise ConnectionError("Connection closed")

    async def incrby(self, key: str, amount: int) -> int:  # noqa: ARG002
        raise ConnectionError("Connection closed")


@pytest.mark.asyncio
async def test_record_spend_survives_closed_redis_during_shutdown(sqlite_db):
    """Lifespan aclose() race: in-flight record_spend still writes SQLite."""
    _, db_path = sqlite_db

    # record_spend must not raise even though every Redis op errors.
    await record_spend(db_path, "alice", 42, "gpt-4o-mini", "openai", redis=_ClosedRedis())

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
            ("alice",),
        ) as cur:
            (total,) = await cur.fetchone()  # type: ignore[assignment]
    assert total == 42, (
        "SQLite ledger must remain authoritative even if Redis was closed "
        "by the lifespan shutdown handler before the background record_spend ran."
    )


# ---------------------------------------------------------------------------
# Failure mode 10: memory pressure — _reserved grows with unique aliases.
#
# release_reservation computes max(0, held - amount) and stores the
# result back — it never removes the key. With a stream of unique aliases
# the dict grows without bound. Documented (xfail strict) as a known
# concern to be fixed with a bounded LRU / TTL cleanup.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserved_dict_bounded_after_release_spendcap(sqlite_db):
    """SpendCapRule._reserved must drop zero-valued entries on release.

    Bug worthless-pymy: prior to the fix, release_reservation set
    _reserved[alias] = 0 but kept the key, leaking one dict entry per
    alias ever seen. This test walks 500 unique aliases through
    evaluate → release and asserts no dead entries remain.
    """
    db, _ = sqlite_db
    r = _FakeRedis()
    rule = SpendCapRule(db=db, redis=r)
    body = b'{"model":"gpt-4","max_tokens":10}'

    for i in range(500):
        alias = f"alias-{i}"
        await _configure_cap(db, alias, 1000.0)
        assert await rule.evaluate(alias, object(), provider="openai", body=body) is None
        await rule.release_reservation(alias, 10)

    assert len(rule._reserved) == 0, (
        f"_reserved has {len(rule._reserved)} dead entries after full releases."
    )


@pytest.mark.asyncio
async def test_reserved_dict_bounded_after_release_tokenbudget(sqlite_db):
    """TokenBudgetRule._reserved must drop zero-valued entries on release.

    Same bug as SpendCapRule — shares the release_reservation pattern.
    """
    db, _ = sqlite_db
    rule = TokenBudgetRule(db=db)
    body = b'{"model":"gpt-4","max_tokens":10}'

    for i in range(500):
        alias = f"alias-{i}"
        await db.execute(
            "INSERT INTO enrollment_config (key_alias, token_budget_daily) VALUES (?, ?)",
            (alias, 10_000),
        )
        await db.commit()
        assert await rule.evaluate(alias, object(), provider="openai", body=body) is None
        await rule.release_reservation(alias, 10)

    assert len(rule._reserved) == 0, (
        f"_reserved has {len(rule._reserved)} dead entries after full releases."
    )


@pytest.mark.asyncio
async def test_reserved_dict_keeps_entries_with_outstanding_reservation(sqlite_db):
    """release_reservation must NOT drop entries that still have held tokens.

    Guard against an overzealous fix that deletes every entry. Only
    zero-valued entries should go.
    """
    db, _ = sqlite_db
    r = _FakeRedis()
    rule = SpendCapRule(db=db, redis=r)
    body = b'{"model":"gpt-4","max_tokens":100}'

    await _configure_cap(db, "alice", 1000.0)
    assert await rule.evaluate("alice", object(), provider="openai", body=body) is None
    # Partial release — 60 held, 40 returned. Entry must survive.
    await rule.release_reservation("alice", 40)

    assert "alice" in rule._reserved
    assert rule._reserved["alice"] == 60
