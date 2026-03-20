"""Tests for the rules engine — spend cap, rate limit, and pipeline behavior."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

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
    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=1000.0, total_tokens=500)

    rule = SpendCapRule(db_path=db_path)
    result = await rule.evaluate("k1", object())
    assert result is None


@pytest.mark.asyncio
async def test_spend_cap_exceeded(tmp_path):
    """Spend at or above cap -> 402 denial."""
    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=100.0, total_tokens=150)

    rule = SpendCapRule(db_path=db_path)
    result = await rule.evaluate("k1", object())
    assert result is not None
    assert result.status_code == 402
    body = json.loads(result.body)
    assert "spend cap" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_spend_cap_null_no_cap(tmp_path):
    """NULL spend_cap -> no limit -> None (pass)."""
    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias="k1", spend_cap=None, total_tokens=999999)

    rule = SpendCapRule(db_path=db_path)
    result = await rule.evaluate("k1", object())
    assert result is None


@pytest.mark.asyncio
async def test_spend_cap_no_enrollment_record(tmp_path):
    """Alias with no enrollment_config row -> pass (no cap configured)."""
    db_path = str(tmp_path / "test.db")
    await _setup_spend_db(db_path, alias=None, spend_cap=None, total_tokens=0)

    rule = SpendCapRule(db_path=db_path)
    result = await rule.evaluate("unknown-alias", object())
    assert result is None


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
                    "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
                    (alias, total_tokens, "gpt-4", "openai"),
                )
        await db.commit()
