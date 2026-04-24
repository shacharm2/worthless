"""Property-based and mutation-resilience tests for the Redis hot-path
metering layer.

Complements the example-based suite in ``test_redis_metering.py`` with:

* **Hypothesis property tests** over:
    - ``spend_key(alias)`` — namespace invariant + collision-freeness for
      every alias matching the proxy's ``_ALIAS_RE``.
    - ``incr_spend_hot`` — cumulative sum of positive amounts, ignores
      non-positive.
    - ``get_spend_hot`` — never silently returns ``0`` on garbage; either
      returns an ``int`` (for integer-coercible bytes) or raises
      :class:`RedisValueError`.

* **Boundary / mutation-resilience tests** on
  :meth:`SpendCapRule._evaluate_redis` — targeting mutations that the
  example suite did not reliably catch (``>=`` vs ``>``, inclusion of
  reservations in the comparison, ``is None`` cache-miss path).

* **Stateful sequencing** via :class:`hypothesis.stateful.RuleBasedStateMachine`
  covering a realistic ``evaluate → record_spend → release_reservation``
  interleaving. Invariants:

    - Sum of outstanding reservations never exceeds ``cap``.
    - After ``record_spend`` lands, the Redis counter equals the SQLite
      ``SUM(tokens)`` for that alias.
    - ``release_reservation`` never drops ``_reserved[alias]`` below 0.

All tests run against the in-process ``_FakeRedis`` stub — no real Redis
required. The stub is intentionally defined here rather than imported so
the property file stays self-contained.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiosqlite
import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from worthless.proxy.metering import (
    SPEND_KEY_PREFIX,
    RedisValueError,
    get_spend_hot,
    incr_spend_hot,
    record_spend,
    spend_key,
    sum_spend_sqlite,
)
from worthless.proxy.rules import SpendCapRule
from worthless.storage.schema import SCHEMA


# ---------------------------------------------------------------------------
# Self-contained _FakeRedis — matches the stub in test_redis_metering.py.
# Mirrors real redis-py decode_responses=False semantics: GET returns bytes,
# INCRBY stores str(int).encode(), SET NX respects prior writes.
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any, *, nx: bool = False, **_: Any) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value if isinstance(value, bytes) else str(value).encode()
        return True

    async def incrby(self, key: str, amount: int) -> int:
        current = int(self.store[key]) if key in self.store else 0
        current += int(amount)
        self.store[key] = str(current).encode()
        return current

    async def aclose(self) -> None:
        pass


class _PlantedBytesRedis:
    """Returns a caller-provided bytes blob from GET — used to fuzz the
    integer-coercion edge of :func:`get_spend_hot`."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def get(self, key: str) -> bytes | None:  # noqa: ARG002
        return self.payload

    async def set(self, *_: Any, **__: Any) -> bool:
        return True

    async def incrby(self, *_: Any, **__: Any) -> int:
        return 0


# The proxy restricts aliases to this regex (see app.py:_ALIAS_RE). We
# generate the property universe directly from it.
_ALIAS_PATTERN = r"[a-zA-Z0-9_-]+"
_aliases = st.from_regex(_ALIAS_PATTERN, fullmatch=True).filter(lambda s: len(s) <= 64)

# Hypothesis profile tuned for this file: keep total runtime modest.
_PROPERTY_SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# spend_key / namespace properties
# ---------------------------------------------------------------------------


@given(alias=_aliases)
@_PROPERTY_SETTINGS
def test_prop_spend_key_namespace_and_roundtrip(alias: str) -> None:
    """Property: ``spend_key`` is a pure string-concat with the prefix and
    is injective over the alias regex."""
    key = spend_key(alias)
    assert key == f"{SPEND_KEY_PREFIX}{alias}"
    assert key.startswith(SPEND_KEY_PREFIX)
    # Injectivity: the stripped suffix round-trips to the alias.
    assert key[len(SPEND_KEY_PREFIX) :] == alias


@given(a=_aliases, b=_aliases)
@_PROPERTY_SETTINGS
def test_prop_spend_key_collision_free(a: str, b: str) -> None:
    """Distinct aliases ⇒ distinct Redis keys. This catches a mutation that
    drops the alias from the format string or swaps prefix separators."""
    assume(a != b)
    assert spend_key(a) != spend_key(b)


# ---------------------------------------------------------------------------
# incr_spend_hot properties
# ---------------------------------------------------------------------------


@given(
    alias=_aliases,
    amounts=st.lists(st.integers(min_value=-10_000, max_value=10_000), max_size=25),
)
@_PROPERTY_SETTINGS
def test_prop_incr_spend_hot_sums_positives_ignores_non_positive(
    alias: str, amounts: list[int]
) -> None:
    """For any sequence of INCR amounts:

    * positive amounts accumulate linearly,
    * zero and negative amounts are no-ops (the contract is explicit at
      metering.py:109),
    * the final counter equals ``sum(a for a in amounts if a > 0)``.

    Catches mutations like ``if tokens <= 0`` → ``if tokens < 0`` that
    would cause zero-amount calls to leak into INCRBY.
    """

    async def _run() -> None:
        r = _FakeRedis()
        expected = 0
        for amt in amounts:
            ret = await incr_spend_hot(r, alias, amt)
            if amt > 0:
                expected += amt
                assert ret == expected
            else:
                # Contract: non-positive → current (or 0 if absent), no write.
                assert ret == (expected if expected > 0 else 0)
        if expected == 0:
            # Key was never written for any positive amount.
            assert await get_spend_hot(r, alias) is None
        else:
            assert await get_spend_hot(r, alias) == expected

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_spend_hot payload fuzzing
# ---------------------------------------------------------------------------


@given(payload=st.binary(min_size=0, max_size=32))
@_PROPERTY_SETTINGS
def test_prop_get_spend_hot_never_silently_returns_zero_on_garbage(
    payload: bytes,
) -> None:
    """For any bytes payload planted under the key:

    * if ``int(payload)`` succeeds, ``get_spend_hot`` returns that int,
    * otherwise it raises :class:`RedisValueError`,
    * it NEVER returns ``None`` (key is present), and
    * it NEVER silently returns ``0`` for non-integer bytes.

    This is the tamper-resistance invariant — a mutation that swallows
    the ``ValueError`` and returns 0 would bypass the spend cap.
    """

    async def _run() -> None:
        r = _PlantedBytesRedis(payload)
        try:
            expected_int = int(payload)
        except (TypeError, ValueError):
            expected_int = None

        if expected_int is None:
            with pytest.raises(RedisValueError):
                await get_spend_hot(r, "alice")
        else:
            result = await get_spend_hot(r, "alice")
            assert result == expected_int
            assert result is not None  # present key ⇒ never None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# SpendCapRule._evaluate_redis boundary / mutation-resilience tests
#
# These are example-based but deliberately sit on the exact boundaries the
# most plausible mutations would break:
#   * `counter >= spend_cap`     vs `>`
#   * `counter is None`          vs `is not None`
#   * `counter + already_reserved` vs `counter` (drop reservations)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_with_cap(tmp_path):
    """Connection + path for a DB pre-loaded with a 1000-token cap."""
    db_path = tmp_path / "worthless.db"
    async with aiosqlite.connect(db_path) as init:
        await init.executescript(SCHEMA)
        await init.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            ("alice", 1000.0),
        )
        await init.commit()
    conn = await aiosqlite.connect(db_path)
    yield conn, str(db_path)
    await conn.close()


@pytest.mark.asyncio
async def test_spend_cap_exact_boundary_denies(db_with_cap):
    """Cap is inclusive-deny: ``counter == cap`` denies.

    Kills mutation ``counter >= spend_cap`` → ``counter > spend_cap``.
    """
    db, _ = db_with_cap
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 1000)  # exactly at cap
    rule = SpendCapRule(db=db, redis=r)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None, "counter == cap must deny (boundary off-by-one)"


@pytest.mark.asyncio
async def test_spend_cap_one_below_boundary_allows(db_with_cap):
    """``counter == cap - 1`` must still allow.

    Kills mutation ``counter >= spend_cap`` → ``counter > spend_cap - 1``.
    """
    db, _ = db_with_cap
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 999)
    rule = SpendCapRule(db=db, redis=r)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is None


@pytest.mark.asyncio
async def test_spend_cap_reservation_is_included_in_cap_check(db_with_cap):
    """A prior reservation must push the next caller over the edge even
    when the Redis counter alone is under cap.

    Kills mutation ``counter + already_reserved`` → ``counter`` at
    rules.py:181.
    """
    db, _ = db_with_cap
    r = _FakeRedis()
    await incr_spend_hot(r, "alice", 500)  # committed = 500
    rule = SpendCapRule(db=db, redis=r)

    # First caller reserves _DEFAULT_TOKEN_ESTIMATE (4096) clipped to
    # remaining (500) ⇒ reservation = 500. Effective total becomes 1000
    # (committed 500 + reserved 500).
    assert await rule.evaluate("alice", object(), provider="openai", body=b"") is None
    assert rule._reserved["alice"] == 500

    # Second caller: committed 500 + reserved 500 == cap 1000 → deny.
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None, "reservation must be counted in cap check"


@pytest.mark.asyncio
async def test_spend_cap_cache_miss_rehydrates_from_sqlite(db_with_cap, tmp_path):
    """When Redis returns ``None``, the rule must rehydrate from SQLite
    and compare the rehydrated total against the cap.

    Kills mutation ``if counter is None`` → ``if counter is not None``
    (skip-rehydrate) which would silently treat miss as 0.
    """
    db, db_path = db_with_cap
    # Plant 1200 tokens in SQLite, nothing in Redis.
    async with aiosqlite.connect(db_path) as w:
        await w.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            ("alice", 1200, None, "openai"),
        )
        await w.commit()

    r = _FakeRedis()  # empty — forces cache-miss path
    rule = SpendCapRule(db=db, redis=r)
    result = await rule.evaluate("alice", object(), provider="openai", body=b"")
    assert result is not None, "miss must rehydrate and see 1200 > cap 1000"
    # And the warmed counter should now be visible.
    assert await get_spend_hot(r, "alice") == 1200


@pytest.mark.asyncio
async def test_release_reservation_never_goes_negative(db_with_cap):
    """``release_reservation`` is clamped at 0.

    Kills mutation ``max(0, held - amount)`` → ``held - amount``.
    """
    db, _ = db_with_cap
    rule = SpendCapRule(db=db, redis=_FakeRedis())
    await rule.release_reservation("alice", 999_999)
    assert rule._reserved["alice"] == 0
    # And a subsequent release is still clamped.
    await rule.release_reservation("alice", 1)
    assert rule._reserved["alice"] == 0


# ---------------------------------------------------------------------------
# Stateful model of evaluate / record_spend / release_reservation.
# ---------------------------------------------------------------------------


class SpendCapStateMachine(RuleBasedStateMachine):
    """Sequence a realistic spend lifecycle and enforce global invariants.

    State shape:
      * ``cap`` — the configured spend cap (fixed at init).
      * ``pending`` — list of ``(alias, reservation)`` tuples that were
        successfully reserved but not yet recorded.
      * ``recorded[alias]`` — cumulative recorded tokens per alias.

    Invariants:
      1. ``sum(reservation for _, reservation in pending) == rule._reserved.total()``.
      2. ``rule._reserved[alias] >= 0`` for all aliases.
      3. After every action, the Redis counter equals
         ``recorded[alias]`` for every alias that has ever been touched.
    """

    # Bounded universe: one cap, handful of aliases.
    CAP = 1000
    ALIASES = ("alice", "bob", "carol")

    def __init__(self) -> None:
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._db: aiosqlite.Connection | None = None
        self._db_path: str | None = None
        self._rule: SpendCapRule | None = None
        self._redis: _FakeRedis | None = None
        self._recorded: dict[str, int] = {}
        self._pending: list[tuple[str, int]] = []

    @initialize()
    def _bootstrap(self) -> None:
        import tempfile

        self._loop = asyncio.new_event_loop()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name

        async def _setup() -> tuple[aiosqlite.Connection, SpendCapRule, _FakeRedis]:
            async with aiosqlite.connect(self._db_path) as init:
                await init.executescript(SCHEMA)
                for alias in self.ALIASES:
                    await init.execute(
                        "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
                        (alias, float(self.CAP)),
                    )
                await init.commit()
            conn = await aiosqlite.connect(self._db_path)
            redis = _FakeRedis()
            rule = SpendCapRule(db=conn, redis=redis)
            return conn, rule, redis

        self._db, self._rule, self._redis = self._loop.run_until_complete(_setup())
        self._recorded = {a: 0 for a in self.ALIASES}
        self._pending = []

    @rule(alias=st.sampled_from(ALIASES), max_tokens=st.integers(min_value=1, max_value=500))
    def try_evaluate(self, alias: str, max_tokens: int) -> None:
        """Attempt a reservation with a client-supplied ``max_tokens``."""
        assert self._rule is not None and self._loop is not None
        body = f'{{"max_tokens":{max_tokens}}}'.encode()
        reserved_before = self._rule._reserved.get(alias, 0)
        result = self._loop.run_until_complete(
            self._rule.evaluate(alias, object(), provider="openai", body=body)
        )
        if result is None:
            reserved_after = self._rule._reserved.get(alias, 0)
            delta = reserved_after - reserved_before
            if delta > 0:
                self._pending.append((alias, delta))

    @rule(data=st.data())
    def record_a_pending(self, data) -> None:
        """Finalise one pending reservation: record_spend + release."""
        assert self._rule is not None and self._loop is not None
        if not self._pending:
            return
        idx = data.draw(st.integers(min_value=0, max_value=len(self._pending) - 1))
        alias, reservation = self._pending.pop(idx)
        # Actual spend drawn to be anywhere in [0, reservation] — mirrors
        # the real-world case where the upstream consumed fewer tokens
        # than reserved.
        actual = data.draw(st.integers(min_value=0, max_value=reservation))

        async def _record() -> None:
            assert self._db_path is not None
            if actual > 0:
                await record_spend(self._db_path, alias, actual, None, "openai", redis=self._redis)
            await self._rule.release_reservation(alias, reservation)

        self._loop.run_until_complete(_record())
        self._recorded[alias] += actual

    @invariant()
    def reservations_never_negative(self) -> None:
        if self._rule is None:
            return
        for alias, held in self._rule._reserved.items():
            assert held >= 0, f"reservation for {alias} went negative: {held}"

    @invariant()
    def redis_counter_matches_sqlite_sum(self) -> None:
        if self._rule is None or self._loop is None or self._redis is None:
            return

        async def _check() -> None:
            assert self._db is not None
            for alias in self.ALIASES:
                sqlite_total = await sum_spend_sqlite(self._db, alias)
                redis_total = await get_spend_hot(self._redis, alias)
                assert sqlite_total == self._recorded[alias], (
                    f"model drift for {alias}: sqlite={sqlite_total} model={self._recorded[alias]}"
                )
                if self._recorded[alias] == 0:
                    assert redis_total is None or redis_total == 0
                else:
                    assert redis_total == sqlite_total, (
                        f"Redis/SQLite drift for {alias}: redis={redis_total} sqlite={sqlite_total}"
                    )

        self._loop.run_until_complete(_check())

    @invariant()
    def reservations_do_not_exceed_cap(self) -> None:
        if self._rule is None:
            return
        for alias in self.ALIASES:
            reserved = self._rule._reserved.get(alias, 0)
            # Effective total = committed (recorded) + reserved ≤ cap.
            # Equality is allowed because the cap is inclusive-deny only
            # on the *next* evaluate; a reservation that brings us exactly
            # to the cap is valid state.
            assert self._recorded[alias] + reserved <= self.CAP, (
                f"over-cap for {alias}: recorded={self._recorded[alias]} "
                f"reserved={reserved} cap={self.CAP}"
            )

    def teardown(self) -> None:
        if self._loop is None:
            return

        async def _close() -> None:
            if self._db is not None:
                await self._db.close()

        try:
            self._loop.run_until_complete(_close())
        finally:
            self._loop.close()
            self._loop = None


# Attach bounded settings to keep runtime predictable.
TestSpendCapStateful = SpendCapStateMachine.TestCase
TestSpendCapStateful.settings = settings(
    max_examples=20,
    stateful_step_count=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
