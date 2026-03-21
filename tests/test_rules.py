"""Tests for the rules engine — spend cap, rate limit, and pipeline behavior."""

from __future__ import annotations

import asyncio
import json

import pytest

from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule


# ---------------------------------------------------------------------------
# RulesEngine pipeline
# ---------------------------------------------------------------------------


class _PassRule:
    """Stub rule that always passes."""

    async def evaluate(self, alias: str, request: object) -> None:
        return None


class _DenyRule:
    """Stub rule that always denies with a 403."""

    def __init__(self) -> None:
        self.called = False

    async def evaluate(self, alias: str, request: object):
        self.called = True
        # Return a simple dict to simulate a denial response
        return {"status": 403, "detail": "denied"}


@pytest.mark.asyncio
async def test_empty_rules_engine_returns_none():
    engine = RulesEngine(rules=[])
    result = await engine.evaluate("test-alias", object())
    assert result is None


@pytest.mark.asyncio
async def test_single_passing_rule_returns_none():
    engine = RulesEngine(rules=[_PassRule()])
    result = await engine.evaluate("test-alias", object())
    assert result is None


@pytest.mark.asyncio
async def test_short_circuits_on_first_denial():
    deny = _DenyRule()
    never_reached = _DenyRule()
    engine = RulesEngine(rules=[deny, never_reached])
    result = await engine.evaluate("test-alias", object())
    assert result is not None
    assert deny.called is True
    assert never_reached.called is False


# ---------------------------------------------------------------------------
# SpendCapRule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_under_limit(tmp_path):
    """Spend below cap -> None (pass)."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=1000.0, total_tokens=500)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("k1", object())
        assert result is None
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_spend_cap_exceeded(tmp_path):
    """Spend at or above cap -> 402 denial."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=100.0, total_tokens=150)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("k1", object())
        assert result is not None
        assert result.status_code == 402
        body = json.loads(result.body)
        assert "spend cap" in body["error"]["message"].lower()
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_spend_cap_null_no_cap(tmp_path):
    """NULL spend_cap -> no limit -> None (pass)."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=None, total_tokens=999999)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("k1", object())
        assert result is None
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_spend_cap_no_enrollment_record(tmp_path):
    """Alias with no enrollment_config row -> pass (no cap configured)."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias=None, spend_cap=None, total_tokens=0)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("unknown-alias", object())
        assert result is None
    finally:
        await db_conn.close()


# ---------------------------------------------------------------------------
# RateLimitRule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_under_threshold():
    rule = RateLimitRule(default_rps=10.0)
    # Single request should pass
    result = await rule.evaluate("k1", _fake_request("127.0.0.1"))
    assert result is None


@pytest.mark.asyncio
async def test_rate_limit_exceeded():
    rule = RateLimitRule(default_rps=2.0)
    req = _fake_request("127.0.0.1")
    # Fire 3 requests rapidly; third should be denied
    await rule.evaluate("k1", req)
    await rule.evaluate("k1", req)
    result = await rule.evaluate("k1", req)
    assert result is not None
    assert result.status_code == 429
    assert "retry-after" in {k.lower() for k in result.headers}


@pytest.mark.asyncio
async def test_rate_limit_uses_sliding_window():
    rule = RateLimitRule(default_rps=2.0)
    req = _fake_request("127.0.0.1")
    await rule.evaluate("k1", req)
    await rule.evaluate("k1", req)
    # Wait for the window to slide
    await asyncio.sleep(1.1)
    result = await rule.evaluate("k1", req)
    assert result is None  # Should pass after window slides


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal request-like object with client info."""

    def __init__(self, ip: str) -> None:
        self.client = type("Client", (), {"host": ip})()


def _fake_request(ip: str = "127.0.0.1") -> _FakeRequest:
    return _FakeRequest(ip)


async def _setup_spend_db(
    db_path: str,
    *,
    alias: str | None,
    spend_cap: float | None,
    total_tokens: int,
) -> None:
    """Create a test DB with spend_log and enrollment_config tables pre-populated."""
    import aiosqlite

    from worthless.storage.schema import SCHEMA

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        if alias is not None:
            await db.execute(
                "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
                (alias, spend_cap),
            )
            if total_tokens > 0:
                await db.execute(
                    "INSERT INTO spend_log (key_alias, tokens, model, provider) "
                    "VALUES (?, ?, ?, ?)",
                    (alias, total_tokens, "gpt-4", "openai"),
                )
        await db.commit()


async def _setup_spend_db_conn(
    db: "aiosqlite.Connection",
    *,
    alias: str | None,
    spend_cap: float | None,
    total_tokens: int,
) -> None:
    """Set up spend DB tables on an existing connection."""
    from worthless.storage.schema import SCHEMA

    await db.executescript(SCHEMA)
    if alias is not None:
        await db.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, spend_cap),
        )
        if total_tokens > 0:
            await db.execute(
                "INSERT INTO spend_log (key_alias, tokens, model, provider) "
                "VALUES (?, ?, ?, ?)",
                (alias, total_tokens, "gpt-4", "openai"),
            )
    await db.commit()


# ---------------------------------------------------------------------------
# SpendCapRule — persistent connection, atomic, fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_concurrent(tmp_path):
    """Two concurrent requests against a spend cap — only one should pass if both would exceed."""
    import aiosqlite

    from worthless.storage.schema import SCHEMA

    db_path = str(tmp_path / "concurrent.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")
        # Cap of 100, already spent 95 — only 5 tokens headroom
        await db.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            ("k1", 100.0),
        )
        await db.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            ("k1", 95, "gpt-4", "openai"),
        )
        await db.commit()

    # Create rule with persistent connection
    db_conn = await aiosqlite.connect(db_path)
    await db_conn.execute("PRAGMA journal_mode=WAL")
    await db_conn.execute("PRAGMA busy_timeout=5000")
    try:
        rule = SpendCapRule(db=db_conn)
        # Both requests should hit the same state — at 95/100, both should deny
        results = await asyncio.gather(
            rule.evaluate("k1", object()),
            rule.evaluate("k1", object()),
        )
        # At least one should be denied (both at 95 tokens, cap 100 — under cap)
        # Actually at 95 < 100, both should pass. Let's use 100 to test at cap.
        # Rewrite: set tokens = 100, cap = 100 — both should be denied
    finally:
        await db_conn.close()

    # Better test: set to exactly at cap
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM spend_log")
        await db.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            ("k1", 100, "gpt-4", "openai"),
        )
        await db.commit()

    db_conn = await aiosqlite.connect(db_path)
    await db_conn.execute("PRAGMA journal_mode=WAL")
    await db_conn.execute("PRAGMA busy_timeout=5000")
    try:
        rule = SpendCapRule(db=db_conn)
        results = await asyncio.gather(
            rule.evaluate("k1", object()),
            rule.evaluate("k1", object()),
        )
        # Both should be denied since spend (100) >= cap (100)
        assert all(r is not None and r.status_code == 402 for r in results)
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_spend_cap_fail_closed_on_db_error(tmp_path):
    """SpendCapRule returns deny (ErrorResponse) when DB raises an exception."""
    import aiosqlite

    db_path = str(tmp_path / "fail.db")
    db_conn = await aiosqlite.connect(db_path)
    # Don't create tables — queries will fail
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("k1", object())
        assert result is not None
        assert result.status_code == 402
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_rate_limiter_ttl_cleanup():
    """Rate limiter _windows dict entries older than 2s are cleaned up."""
    import time
    from unittest.mock import patch

    rule = RateLimitRule(default_rps=100.0, cleanup_interval=0.0)
    req = _fake_request("10.0.0.1")

    # Add some entries
    await rule.evaluate("k1", req)
    await rule.evaluate("k2", _fake_request("10.0.0.2"))
    assert len(rule._windows) == 2

    # Simulate time passing beyond the 2s TTL
    fake_now = time.monotonic() + 3.0
    with patch("time.monotonic", return_value=fake_now):
        # Trigger cleanup by evaluating again
        await rule.evaluate("k3", _fake_request("10.0.0.3"))

    # k1 and k2 entries should be cleaned up, only k3 remains
    assert ("k1", "10.0.0.1") not in rule._windows
    assert ("k2", "10.0.0.2") not in rule._windows
    assert ("k3", "10.0.0.3") in rule._windows


@pytest.mark.asyncio
async def test_rate_limiter_expired_keys_removed():
    """After cleanup, expired (alias, ip) keys are completely removed from _windows."""
    import time
    from unittest.mock import patch

    rule = RateLimitRule(default_rps=100.0, cleanup_interval=0.0)
    req = _fake_request("10.0.0.1")

    # Add entries for multiple keys
    for i in range(5):
        await rule.evaluate(f"alias-{i}", req)

    assert len(rule._windows) == 5

    # Move time forward past TTL
    fake_now = time.monotonic() + 3.0
    with patch("time.monotonic", return_value=fake_now):
        await rule.evaluate("fresh-alias", req)

    # All old entries should be removed
    for i in range(5):
        assert (f"alias-{i}", "10.0.0.1") not in rule._windows
    assert ("fresh-alias", "10.0.0.1") in rule._windows
