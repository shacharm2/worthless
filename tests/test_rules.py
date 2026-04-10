"""Tests for the rules engine — spend cap, rate limit, and pipeline behavior."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import pytest

from worthless.proxy.errors import (
    time_window_error_response,
    token_budget_error_response,
)
from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule, TokenBudgetRule


# ---------------------------------------------------------------------------
# RulesEngine pipeline
# ---------------------------------------------------------------------------


class _PassRule:
    """Stub rule that always passes."""

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ) -> None:
        return None


class _DenyRule:
    """Stub rule that always denies with a 403."""

    def __init__(self) -> None:
        self.called = False

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ):
        self.called = True
        # Return a simple dict to simulate a denial response
        return {"status": 403, "detail": "denied"}


class _BodyCapturingRule:
    """Stub rule that captures the body it receives."""

    def __init__(self) -> None:
        self.received_body: bytes | None = None

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ):
        self.received_body = body
        return None


@pytest.mark.asyncio
async def test_empty_rules_engine_returns_none():
    engine = RulesEngine(rules=[])
    result = await engine.evaluate("test-alias", object(), body=b"")
    assert result is None


@pytest.mark.asyncio
async def test_single_passing_rule_returns_none():
    engine = RulesEngine(rules=[_PassRule()])
    result = await engine.evaluate("test-alias", object(), body=b"")
    assert result is None


@pytest.mark.asyncio
async def test_short_circuits_on_first_denial():
    deny = _DenyRule()
    never_reached = _DenyRule()
    engine = RulesEngine(rules=[deny, never_reached])
    result = await engine.evaluate("test-alias", object(), body=b"")
    assert result is not None
    assert deny.called is True
    assert never_reached.called is False


# ---------------------------------------------------------------------------
# Body parameter passthrough (WOR-182)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rules_engine_passes_body_to_rules():
    """RulesEngine.evaluate() must forward body kwarg to each rule."""
    rule = _BodyCapturingRule()
    engine = RulesEngine(rules=[rule])
    test_body = b'{"model": "gpt-4o"}'
    await engine.evaluate("test-alias", object(), body=test_body)
    assert rule.received_body == test_body


@pytest.mark.asyncio
async def test_rules_engine_body_defaults_to_empty():
    """Body defaults to b'' when not provided."""
    rule = _BodyCapturingRule()
    engine = RulesEngine(rules=[rule])
    await engine.evaluate("test-alias", object())
    assert rule.received_body == b""


@pytest.mark.asyncio
async def test_spend_cap_accepts_body_parameter(tmp_path):
    """SpendCapRule.evaluate() accepts body kwarg without error."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=1000.0, total_tokens=500)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b'{"model": "gpt-4"}')
        assert result is None
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_rate_limit_accepts_body_parameter():
    """RateLimitRule.evaluate() accepts body kwarg without error."""
    rule = RateLimitRule(default_rps=10.0)
    result = await rule.evaluate("k1", _fake_request("127.0.0.1"), body=b'{"model": "gpt-4"}')
    assert result is None


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
        result = await rule.evaluate("k1", object(), body=b"")
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
        result = await rule.evaluate("k1", object(), body=b"")
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
        result = await rule.evaluate("k1", object(), body=b"")
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
        result = await rule.evaluate("unknown-alias", object(), body=b"")
        assert result is None
    finally:
        await db_conn.close()


# ---------------------------------------------------------------------------
# TokenBudgetRule (WOR-160)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_budget_all_null_passes(tmp_path):
    """All budget columns NULL → no limit → pass."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=None, total_tokens=999999)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is None
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_under_daily_limit(tmp_path):
    """Tokens below daily budget → pass."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_token_budget_db(db_path, alias="k1", daily=100000, tokens_today=50000)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is None
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_daily_exceeded(tmp_path):
    """Tokens at or above daily budget → 429 with usage stats."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_token_budget_db(db_path, alias="k1", daily=100000, tokens_today=100000)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is not None
        assert result.status_code == 429
        body = json.loads(result.body)
        assert "daily" in body["error"]["message"]
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_weekly_exceeded(tmp_path):
    """Weekly budget exceeded → 429."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_token_budget_db(db_path, alias="k1", weekly=500000, tokens_this_week=500000)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is not None
        assert result.status_code == 429
        body = json.loads(result.body)
        assert "weekly" in body["error"]["message"]
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_monthly_exceeded(tmp_path):
    """Monthly budget exceeded → 429."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_token_budget_db(db_path, alias="k1", monthly=2000000, tokens_this_month=2000000)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is not None
        assert result.status_code == 429
        body = json.loads(result.body)
        assert "monthly" in body["error"]["message"]
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_daily_ok_monthly_exceeded(tmp_path):
    """Daily under limit but monthly exceeded → 429 monthly."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_token_budget_db(
        db_path,
        alias="k1",
        daily=100000,
        monthly=500000,
        tokens_today=50000,
        tokens_this_month=500000,
    )

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is not None
        assert result.status_code == 429
        body = json.loads(result.body)
        assert "monthly" in body["error"]["message"]
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_old_records_not_counted(tmp_path):
    """Spend records older than the window are not counted."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_token_budget_db(db_path, alias="k1", daily=100000, tokens_old=999999)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is None  # Old tokens don't count
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_no_enrollment_config(tmp_path):
    """No enrollment_config row → pass (no budget configured)."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_spend_db(db_path, alias=None, spend_cap=None, total_tokens=0)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("unknown-alias", object(), body=b"")
        assert result is None
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_fail_closed_on_db_error(tmp_path):
    """DB error → fail closed (429)."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    db_conn = await aiosqlite.connect(db_path)
    # Don't create tables — queries will fail
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is not None
        assert result.status_code == 429
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_token_budget_anthropic_error_format(tmp_path):
    """Anthropic provider → Anthropic error format."""
    import aiosqlite

    db_path = str(tmp_path / "tb.db")
    await _setup_token_budget_db(db_path, alias="k1", daily=100, tokens_today=200)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = TokenBudgetRule(db=db_conn)
        result = await rule.evaluate("k1", object(), provider="anthropic", body=b"")
        assert result is not None
        assert result.status_code == 429
        body = json.loads(result.body)
        assert body["type"] == "error"
        assert "daily" in body["error"]["message"]
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
    # Advance time past the 1s window without sleeping
    fake_now = time.monotonic() + 1.1
    with patch("time.monotonic", return_value=fake_now):
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


@pytest.mark.asyncio
async def test_spend_cap_returns_anthropic_error_format(tmp_path):
    """When provider=anthropic, spend cap denial uses Anthropic error format."""
    import aiosqlite

    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=100.0, total_tokens=150)

    db_conn = await aiosqlite.connect(db_path)
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("k1", object(), provider="anthropic", body=b"")
        assert result is not None
        assert result.status_code == 402
        body = json.loads(result.body)
        # Anthropic format: {"type": "error", "error": {"type": ..., "message": ...}}
        assert body["type"] == "error"
        assert "spend cap" in body["error"]["message"].lower()
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_rate_limit_returns_anthropic_error_format():
    """When provider=anthropic, rate limit denial uses Anthropic error format."""
    rule = RateLimitRule(default_rps=1.0)
    req = _fake_request("127.0.0.1")
    await rule.evaluate("k1", req, provider="anthropic", body=b"")
    result = await rule.evaluate("k1", req, provider="anthropic", body=b"")
    assert result is not None
    assert result.status_code == 429
    body = json.loads(result.body)
    assert body["type"] == "error"
    assert "rate limit" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_per_enrollment_rate_limit(tmp_path):
    """Per-enrollment rate_limit_rps from DB overrides default."""
    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=None, total_tokens=0, rate_limit_rps=2.0)

    rule = RateLimitRule(default_rps=100.0, db_path=db_path)
    req = _fake_request("127.0.0.1")
    # First two should pass (per-enrollment limit is 2)
    assert await rule.evaluate("k1", req) is None
    assert await rule.evaluate("k1", req) is None
    # Third should be denied
    result = await rule.evaluate("k1", req)
    assert result is not None
    assert result.status_code == 429


@pytest.mark.asyncio
async def test_per_enrollment_rate_limit_falls_back_to_default(tmp_path):
    """Without per-enrollment config, falls back to default_rps."""
    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias=None, spend_cap=None, total_tokens=0)

    rule = RateLimitRule(default_rps=2.0, db_path=db_path)
    req = _fake_request("127.0.0.1")
    await rule.evaluate("unknown", req)
    await rule.evaluate("unknown", req)
    result = await rule.evaluate("unknown", req)
    assert result is not None
    assert result.status_code == 429


async def _setup_spend_db(
    db_path: str,
    *,
    alias: str | None,
    spend_cap: float | None,
    total_tokens: int,
    rate_limit_rps: float | None = None,
) -> None:
    """Create a test DB with spend_log and enrollment_config tables pre-populated."""
    import aiosqlite

    from worthless.storage.schema import SCHEMA

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        if alias is not None:
            await db.execute(
                "INSERT INTO enrollment_config"
                " (key_alias, spend_cap, rate_limit_rps)"
                " VALUES (?, ?, ?)",
                (alias, spend_cap, rate_limit_rps if rate_limit_rps is not None else 100.0),
            )
            if total_tokens > 0:
                await db.execute(
                    "INSERT INTO spend_log (key_alias, tokens, model, provider) "
                    "VALUES (?, ?, ?, ?)",
                    (alias, total_tokens, "gpt-4", "openai"),
                )
        await db.commit()


async def _setup_token_budget_db(
    db_path: str,
    *,
    alias: str,
    daily: int | None = None,
    weekly: int | None = None,
    monthly: int | None = None,
    tokens_today: int = 0,
    tokens_this_week: int = 0,
    tokens_this_month: int = 0,
    tokens_old: int = 0,
) -> None:
    """Create a test DB with token budget config and time-stamped spend records."""
    import aiosqlite

    from worthless.storage.schema import SCHEMA

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute(
            "INSERT INTO enrollment_config"
            " (key_alias, token_budget_daily, token_budget_weekly,"
            " token_budget_monthly)"
            " VALUES (?, ?, ?, ?)",
            (alias, daily, weekly, monthly),
        )
        # Today's tokens
        if tokens_today > 0:
            await db.execute(
                "INSERT INTO spend_log"
                " (key_alias, tokens, model, provider, created_at)"
                " VALUES (?, ?, ?, ?, datetime('now'))",
                (alias, tokens_today, "gpt-4", "openai"),
            )
        # This week's tokens (3 days ago)
        if tokens_this_week > 0:
            await db.execute(
                "INSERT INTO spend_log"
                " (key_alias, tokens, model, provider, created_at)"
                " VALUES (?, ?, ?, ?, datetime('now', '-3 days'))",
                (alias, tokens_this_week, "gpt-4", "openai"),
            )
        # This month's tokens (15 days ago)
        if tokens_this_month > 0:
            await db.execute(
                "INSERT INTO spend_log"
                " (key_alias, tokens, model, provider, created_at)"
                " VALUES (?, ?, ?, ?, datetime('now', '-15 days'))",
                (alias, tokens_this_month, "gpt-4", "openai"),
            )
        # Old tokens (60 days ago — outside all windows)
        if tokens_old > 0:
            await db.execute(
                "INSERT INTO spend_log"
                " (key_alias, tokens, model, provider, created_at)"
                " VALUES (?, ?, ?, ?, datetime('now', '-60 days'))",
                (alias, tokens_old, "gpt-4", "openai"),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# SpendCapRule — persistent connection, atomic, fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_cap_concurrent_two_connections(tmp_path):
    """Two concurrent requests via separate connections — both over cap get denied."""
    import aiosqlite

    from worthless.storage.schema import SCHEMA

    db_path = str(tmp_path / "concurrent.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")
        # Cap of 100, already spent 100 — both should be denied
        await db.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            ("k1", 100.0),
        )
        await db.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            ("k1", 100, "gpt-4", "openai"),
        )
        await db.commit()

    # Two SEPARATE connections to test real concurrency (not serialized on one conn)
    db_conn1 = await aiosqlite.connect(db_path)
    db_conn2 = await aiosqlite.connect(db_path)
    await db_conn1.execute("PRAGMA journal_mode=WAL")
    await db_conn1.execute("PRAGMA busy_timeout=5000")
    await db_conn2.execute("PRAGMA journal_mode=WAL")
    await db_conn2.execute("PRAGMA busy_timeout=5000")
    try:
        rule1 = SpendCapRule(db=db_conn1)
        rule2 = SpendCapRule(db=db_conn2)
        results = await asyncio.gather(
            rule1.evaluate("k1", object()),
            rule2.evaluate("k1", object()),
        )
        # Both should be denied since spend (100) >= cap (100)
        assert all(r is not None and r.status_code == 402 for r in results)
    finally:
        await db_conn1.close()
        await db_conn2.close()


@pytest.mark.asyncio
async def test_spend_cap_concurrent_under_cap_serialized(tmp_path):
    """At 40/100 spent, two concurrent 60-token-equivalent requests both pass the gate
    (spend cap checks current total, not projected)."""
    import aiosqlite

    from worthless.storage.schema import SCHEMA

    db_path = str(tmp_path / "concurrent2.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            ("k1", 100.0),
        )
        await db.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            ("k1", 40, "gpt-4", "openai"),
        )
        await db.commit()

    db_conn1 = await aiosqlite.connect(db_path)
    db_conn2 = await aiosqlite.connect(db_path)
    await db_conn1.execute("PRAGMA journal_mode=WAL")
    await db_conn1.execute("PRAGMA busy_timeout=5000")
    await db_conn2.execute("PRAGMA journal_mode=WAL")
    await db_conn2.execute("PRAGMA busy_timeout=5000")
    try:
        rule1 = SpendCapRule(db=db_conn1)
        rule2 = SpendCapRule(db=db_conn2)
        results = await asyncio.gather(
            rule1.evaluate("k1", object()),
            rule2.evaluate("k1", object()),
        )
        # Both should pass — 40 < 100 cap
        assert all(r is None for r in results)
    finally:
        await db_conn1.close()
        await db_conn2.close()


@pytest.mark.asyncio
async def test_spend_cap_fail_closed_on_db_error(tmp_path):
    """SpendCapRule returns deny (ErrorResponse) when DB raises an exception."""
    import aiosqlite

    db_path = str(tmp_path / "fail.db")
    db_conn = await aiosqlite.connect(db_path)
    # Don't create tables — queries will fail
    try:
        rule = SpendCapRule(db=db_conn)
        result = await rule.evaluate("k1", object(), body=b"")
        assert result is not None
        assert result.status_code == 402
    finally:
        await db_conn.close()


@pytest.mark.asyncio
async def test_rate_limiter_ttl_cleanup():
    """Rate limiter _windows dict entries older than 2s are cleaned up."""
    import time

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


# ---------------------------------------------------------------------------
# WOR-183: Structured error factories
# ---------------------------------------------------------------------------


def test_token_budget_error_openai():
    """token_budget_error_response returns 429 with period, used, and limit."""
    resp = token_budget_error_response(period="daily", used=85000, limit=100000, provider="openai")
    assert resp.status_code == 429
    body = json.loads(resp.body)
    assert "daily" in body["error"]["message"]
    assert "85,000" in body["error"]["message"] or "85000" in body["error"]["message"]
    assert "100,000" in body["error"]["message"] or "100000" in body["error"]["message"]


def test_token_budget_error_anthropic():
    resp = token_budget_error_response(
        period="weekly", used=500000, limit=500000, provider="anthropic"
    )
    assert resp.status_code == 429
    body = json.loads(resp.body)
    assert body["type"] == "error"
    assert "weekly" in body["error"]["message"]


def test_time_window_error_openai():
    """time_window_error_response returns 403 with current time and window."""
    resp = time_window_error_response(
        current_time="22:15 UTC", window="09:00-17:00", provider="openai"
    )
    assert resp.status_code == 403
    body = json.loads(resp.body)
    assert "22:15" in body["error"]["message"]
    assert "09:00-17:00" in body["error"]["message"]


def test_time_window_error_anthropic():
    resp = time_window_error_response(
        current_time="03:00 EST", window="09:00-17:00", provider="anthropic"
    )
    assert resp.status_code == 403
    body = json.loads(resp.body)
    assert body["type"] == "error"
