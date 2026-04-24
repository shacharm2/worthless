"""Defense-in-depth tests for the Redis metering layer.

Pen-tester findings (post-ship polish):

* worthless-onqa: ``spend_key(alias)`` must validate alias internally —
  defense in depth against any future caller that bypasses the URL
  regex in app.py.
* worthless-5w32: ``SpendDirtyTracker`` must bound its dirty set so
  repeated INCR failures across many unique aliases don't leak memory.
  Same class as worthless-pymy (which capped ``SpendCapRule._reserved``).
* worthless-35j1: ``get_spend_hot`` must reject oversized Redis values
  before ``int(raw)`` even runs — a 10MB digit string would otherwise
  flow through the redis-py buffer and waste work.

All tests here are red against HEAD without the defense fixes, green
after. Each docstring names the bead it closes.
"""

from __future__ import annotations

import pytest

from worthless.proxy.metering import (
    RedisValueError,
    SpendDirtyTracker,
    get_spend_hot,
    spend_key,
)


# ---------------------------------------------------------------------------
# worthless-onqa: spend_key(alias) validates alias
# ---------------------------------------------------------------------------


class TestSpendKeyValidatesAlias:
    """Defense in depth: spend_key rejects alias shapes the URL regex
    blocks. Guards any future caller (admin API, reseed task, test seam)
    that invokes spend_key() with an alias from another source.
    """

    def test_accepts_well_formed_alias(self):
        assert spend_key("alice") == "worthless:spend:alice"
        assert spend_key("proj-1") == "worthless:spend:proj-1"
        assert spend_key("a_b_C-123") == "worthless:spend:a_b_C-123"

    def test_rejects_empty_alias(self):
        with pytest.raises(ValueError, match="alias"):
            spend_key("")

    def test_rejects_colon_injection(self):
        """'worthless:spend:victim' should not be reachable from a crafted alias."""
        with pytest.raises(ValueError, match="alias"):
            spend_key("victim:extra")

    def test_rejects_newline(self):
        with pytest.raises(ValueError, match="alias"):
            spend_key("alice\nevil")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="alias"):
            spend_key("../evil")

    def test_rejects_space(self):
        with pytest.raises(ValueError, match="alias"):
            spend_key("has space")

    def test_rejects_non_string(self):
        with pytest.raises((TypeError, ValueError)):
            spend_key(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# worthless-5w32: SpendDirtyTracker bounded set
# ---------------------------------------------------------------------------


class TestSpendDirtyTrackerBound:
    """Tracker must not grow unboundedly under repeated INCR failures.

    The cap mirrors the _reserved fix (worthless-pymy): oldest entries
    get dropped when a hard ceiling is reached.
    """

    @pytest.mark.asyncio
    async def test_mark_beyond_cap_drops_oldest(self):
        tracker = SpendDirtyTracker(max_entries=10)
        for i in range(50):
            await tracker.mark(f"alias-{i}")
        # Ceiling honoured.
        assert len(tracker._dirty) <= 10

    @pytest.mark.asyncio
    async def test_default_cap_is_reasonable(self):
        """Default cap should be large enough that ordinary traffic doesn't
        hit it, but small enough to bound memory on pathological loads."""
        tracker = SpendDirtyTracker()
        # Just read the default — must be defined and finite.
        assert tracker._max_entries is not None
        assert 1_000 <= tracker._max_entries <= 1_000_000

    @pytest.mark.asyncio
    async def test_most_recent_alias_survives_eviction(self):
        """When cap is hit, the most-recently-marked alias is still present.

        FIFO eviction ensures we don't lose the aliases with fresh drift
        signals in favour of stale ones.
        """
        tracker = SpendDirtyTracker(max_entries=5)
        for i in range(20):
            await tracker.mark(f"alias-{i}")
        # alias-19 was the most recent — must be present.
        assert await tracker.is_dirty("alias-19")

    @pytest.mark.asyncio
    async def test_clear_still_works_after_eviction(self):
        tracker = SpendDirtyTracker(max_entries=5)
        for i in range(20):
            await tracker.mark(f"alias-{i}")
        # Clearing a still-present alias should bring it down.
        assert await tracker.is_dirty("alias-19")
        await tracker.clear("alias-19")
        assert not await tracker.is_dirty("alias-19")


# ---------------------------------------------------------------------------
# worthless-35j1: get_spend_hot rejects oversized raw values
# ---------------------------------------------------------------------------


class _OversizedValueRedis:
    """GET returns a pathological raw payload (e.g. 10KB of digits)."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def get(self, key: str) -> bytes:  # noqa: ARG002
        return self._payload


class TestGetSpendHotCapsRawSize:
    """A sane counter value fits in ~20 bytes. Anything larger is tamper
    (or a Redis client bug) and must not even reach int() parsing."""

    @pytest.mark.asyncio
    async def test_small_value_parses_normally(self):
        r = _OversizedValueRedis(b"12345")
        assert await get_spend_hot(r, "alice") == 12345

    @pytest.mark.asyncio
    async def test_exactly_at_limit_still_parses(self):
        # 32-byte value — at the documented limit. Still parses.
        r = _OversizedValueRedis(b"9" * 32)
        assert await get_spend_hot(r, "alice") == int(b"9" * 32)

    @pytest.mark.asyncio
    async def test_oversized_value_raises_redis_value_error(self):
        """A 10KB digit string must be treated as a cache miss / tamper,
        not int-parsed. Callers already translate RedisValueError into a
        rehydrate from SQLite."""
        r = _OversizedValueRedis(b"9" * 10_000)
        with pytest.raises(RedisValueError, match="oversized"):
            await get_spend_hot(r, "alice")

    @pytest.mark.asyncio
    async def test_just_over_limit_raises(self):
        r = _OversizedValueRedis(b"9" * 33)
        with pytest.raises(RedisValueError, match="oversized"):
            await get_spend_hot(r, "alice")
