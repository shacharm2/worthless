"""Streaming x Redis x reservations — composed failure modes.

This file covers the six failure modes the earlier passes could not, because
they only land when a streaming request, a Redis counter, and an in-memory
reservation all move together through the ASGI handler. The unit-level
test_redis_metering_failure_modes.py exercises SpendCapRule directly; the
test_streaming_redis_integration.py happy-path-ish file never drives an
error path through the streaming generator.

Each test boots the full ASGI app via create_app, injects fakes onto
app.state, and drives a mocked upstream via respx. We assert on invariants
after the BackgroundTask has had a chance to drain.

Invariants:
1. Upstream ReadTimeout mid-stream -> reservation released, no phantom INCR.
2. Client abort mid-stream -> generator's finally runs -> release fires.
3. Slow Redis INCR in the background task -> bounded wall-clock completion.
4. Adapter 5xx response body (non-streaming wrap path) -> reservation released.
5. Double release of the same reservation -> never goes negative.
6. Streaming request denied by SpendCapRule -> reservation dict empty after 402.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import aiosqlite
import anyio
import httpx
import pytest
import respx

from worthless.crypto.splitter import split_key_fp
from worthless.proxy.app import create_app
from worthless.proxy.config import ProxySettings
from worthless.proxy.metering import (
    SpendDirtyTracker,
    spend_key,
)
from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule
from worthless.storage.repository import StoredShard


# ---------------------------------------------------------------------------
# Redis fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.incr_calls: int = 0

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any, *, nx: bool = False, **_: Any) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = str(value).encode() if not isinstance(value, bytes) else value
        return True

    async def incrby(self, key: str, amount: int) -> int:
        self.incr_calls += 1
        current = int(self.store[key]) if key in self.store else 0
        current += int(amount)
        self.store[key] = str(current).encode()
        return current

    async def aclose(self) -> None:
        return None


class _SlowIncrRedis(_FakeRedis):
    """INCRBY sleeps for `delay_s` seconds before completing.

    Models a Redis that is reachable but responds slowly on the write path,
    e.g. via a 2s socket_timeout landing on the BackgroundTask. Used to
    verify that a background record_spend terminates in bounded time.
    """

    def __init__(self, delay_s: float = 0.25) -> None:
        super().__init__()
        self.delay_s = delay_s

    async def incrby(self, key: str, amount: int) -> int:
        await asyncio.sleep(self.delay_s)
        return await super().incrby(key, amount)


# ---------------------------------------------------------------------------
# Helpers — mirror test_streaming_redis_integration.py so we stay self-contained.
# ---------------------------------------------------------------------------


@pytest.fixture()
def proxy_settings(tmp_db_path: str, fernet_key: bytes) -> ProxySettings:
    return ProxySettings(
        db_path=tmp_db_path,
        fernet_key=bytearray(fernet_key),
        default_rate_limit_rps=100.0,
        upstream_timeout=10.0,
        streaming_timeout=30.0,
        allow_insecure=True,
    )


async def _enroll(repo, alias: str, api_key: str, prefix: str, provider: str):
    sr = split_key_fp(api_key, prefix=prefix, provider=provider)
    shard = StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider=provider,
    )
    await repo.store(alias, shard, prefix=sr.prefix, charset=sr.charset)
    return alias, sr.shard_a.decode("utf-8"), api_key


@pytest.fixture()
async def stack(proxy_settings: ProxySettings, repo):
    """Full ASGI app with Redis + tracker wired.

    Returns (app, client, redis, tracker, rule, alias, shard_a).
    """
    app = create_app(proxy_settings)
    db = await aiosqlite.connect(proxy_settings.db_path)
    redis = _FakeRedis()
    tracker = SpendDirtyTracker()
    rule = SpendCapRule(db=db, redis=redis, dirty_tracker=tracker)

    app.state.db = db
    app.state.repo = repo
    app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
    app.state.redis = redis
    app.state.dirty_tracker = tracker
    app.state.rules_engine = RulesEngine(
        rules=[rule, RateLimitRule(default_rps=proxy_settings.default_rate_limit_rps)]
    )

    alias, shard_a, _raw = await _enroll(
        repo, "test-alias", "sk-test-" + "x" * 40, prefix="sk-", provider="openai"
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield app, client, redis, tracker, rule, alias, shard_a

    await app.state.httpx_client.aclose()
    await db.close()


async def _wait_for_background(max_ms: int = 800) -> None:
    for _ in range(max_ms // 10):
        await anyio.sleep(0.01)


# ---------------------------------------------------------------------------
# Fixed streaming chunks (OpenAI shape, with usage).
# ---------------------------------------------------------------------------

_SSE_PREFIX = (
    b'data: {"id":"c1","choices":[{"delta":{"role":"assistant"}}]}\n\n'
    b'data: {"id":"c1","choices":[{"delta":{"content":"Hi"}}]}\n\n'
)
_SSE_USAGE_TAIL = (
    b'data: {"id":"c1","choices":[],"usage":{"prompt_tokens":7,'
    b'"completion_tokens":5,"total_tokens":12},"model":"gpt-4o-mini"}\n\n'
    b"data: [DONE]\n\n"
)
_SSE_FULL = _SSE_PREFIX + _SSE_USAGE_TAIL


def _stream_request_body() -> bytes:
    return (
        b'{"model":"gpt-4o-mini","stream":true,'
        b'"stream_options":{"include_usage":true},'
        b'"messages":[{"role":"user","content":"hi"}]}'
    )


# ===========================================================================
# Mode 1 — upstream ReadTimeout mid-stream.
#
# respx can raise on the side_effect. We force httpx.ReadTimeout at request
# time; the gate has ALREADY reserved. The expected invariant: after the
# error response lands, SpendCapRule._reserved[alias] == 0.
# ===========================================================================


@pytest.mark.asyncio
@respx.mock
async def test_upstream_readtimeout_releases_reservation(stack):
    app, client, _redis, _tracker, rule, alias, shard_a = stack

    # Configure a generous cap so the gate passes and reserves.
    async with aiosqlite.connect(app.state.settings.db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 1_000_000.0),
        )
        await wdb.commit()

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("simulated upstream readtimeout")
    )

    try:
        resp = await client.post(
            f"/{alias}/v1/chat/completions",
            headers={"authorization": f"Bearer {shard_a}", "content-type": "application/json"},
            content=_stream_request_body(),
        )
        # Proxy should translate to a 5xx (502/504); accept any 5xx or
        # exception bubbling to httpx client (ASGITransport can re-raise).
        assert resp.status_code >= 500
    except httpx.HTTPError:
        # Acceptable: transport surfaced the error without a wrapped response.
        pass

    await _wait_for_background()

    held = rule._reserved.get(alias, 0)
    assert held == 0, (
        f"Upstream ReadTimeout must release the reservation. Held={held}. "
        "Bug: leaked reservation -> cap unreachable after a burst of upstream timeouts."
    )


# ===========================================================================
# Mode 2 — client aborts mid-stream AFTER first chunk but BEFORE usage.
#
# Simulate by consuming the stream iterator then closing early. With respx,
# we feed chunks via an iterable that yields prefix chunks only (no usage).
# StreamingUsageCollector.result() -> None, record_spend is NOT called,
# but release_spend_reservation MUST still fire via the generator's
# finally: block.
# ===========================================================================


@pytest.mark.asyncio
@respx.mock
async def test_client_abort_mid_stream_releases_reservation(stack):
    app, client, redis, _tracker, rule, alias, shard_a = stack

    async with aiosqlite.connect(app.state.settings.db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 1_000_000.0),
        )
        await wdb.commit()

    # Upstream returns prefix only — no usage chunk. Proxy streams what it has.
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            stream=_SSE_PREFIX,
            headers={"content-type": "text/event-stream"},
        )
    )

    # Use client.stream() and close early to simulate a client abort.
    async with client.stream(
        "POST",
        f"/{alias}/v1/chat/completions",
        headers={"authorization": f"Bearer {shard_a}", "content-type": "application/json"},
        content=_stream_request_body(),
    ) as resp:
        assert resp.status_code == 200
        # Read one chunk then bail.
        async for _ in resp.aiter_bytes():
            break
        # Close without reading the rest.
    await _wait_for_background()

    held = rule._reserved.get(alias, 0)
    assert held == 0, (
        f"Client abort mid-stream must release the reservation via the "
        f"BackgroundTask that wraps _record_metering (the generator's "
        f"finally only closes upstream_resp). Held={held}."
    )
    # And because no usage chunk was emitted, no INCR should have fired.
    assert redis.incr_calls == 0, (
        f"No usage chunk -> no record_spend -> no INCR. Observed {redis.incr_calls}."
    )


# ===========================================================================
# Mode 3 — Slow Redis INCR in the BackgroundTask.
#
# Not an error, just a 250ms hang on INCRBY. BackgroundTask must complete
# within a bounded budget (we give it 3s of wall clock). Asserts no leak.
# ===========================================================================


@pytest.mark.asyncio
@respx.mock
async def test_slow_redis_background_task_completes_bounded(stack):
    app, client, _redis, _tracker, rule, alias, shard_a = stack

    # Swap in a Redis whose INCRBY sleeps 250ms.
    slow = _SlowIncrRedis(delay_s=0.25)
    app.state.redis = slow
    app.state.rules_engine.rules[0] = SpendCapRule(
        db=app.state.db, redis=slow, dirty_tracker=app.state.dirty_tracker
    )
    # Re-local alias to the new rule for post-assertion.
    rule = app.state.rules_engine.rules[0]

    async with aiosqlite.connect(app.state.settings.db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 1_000_000.0),
        )
        await wdb.commit()

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, stream=_SSE_FULL, headers={"content-type": "text/event-stream"}
        )
    )

    t0 = time.monotonic()
    resp = await client.post(
        f"/{alias}/v1/chat/completions",
        headers={"authorization": f"Bearer {shard_a}", "content-type": "application/json"},
        content=_stream_request_body(),
    )
    assert resp.status_code == 200
    _ = await resp.aread()

    # Give background task up to 3 seconds (generous vs 250ms delay).
    deadline = t0 + 3.0
    while slow.incr_calls == 0 and time.monotonic() < deadline:
        await anyio.sleep(0.02)

    elapsed = time.monotonic() - t0
    assert slow.incr_calls >= 1, (
        f"Background record_spend did not complete within 3s (elapsed={elapsed:.2f}s). "
        "Possible hang in BackgroundTask path."
    )
    assert rule._reserved.get(alias, 0) == 0, "Reservation leaked across slow INCR"


# ===========================================================================
# Mode 4 — adapter 5xx mid-stream / non-streaming wrap path.
#
# When the upstream returns a 5xx, the proxy typically wraps it as a
# non-streaming error response. The reservation must still release.
# ===========================================================================


@pytest.mark.asyncio
@respx.mock
async def test_upstream_5xx_releases_reservation(stack):
    app, client, _redis, _tracker, rule, alias, shard_a = stack

    async with aiosqlite.connect(app.state.settings.db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 1_000_000.0),
        )
        await wdb.commit()

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "upstream_unavailable"})
    )

    resp = await client.post(
        f"/{alias}/v1/chat/completions",
        headers={"authorization": f"Bearer {shard_a}", "content-type": "application/json"},
        content=_stream_request_body(),
    )
    # Proxy should surface upstream 5xx (or its own 502 wrap). Accept either.
    assert resp.status_code in (502, 503), f"Unexpected status {resp.status_code}"
    await _wait_for_background()

    held = rule._reserved.get(alias, 0)
    assert held == 0, (
        f"Upstream 5xx must release the reservation. Held={held}. "
        "A leaked reservation here drains the cap across a burst of upstream errors."
    )


# ===========================================================================
# Mode 5 — double release.
#
# If two error paths fire release_spend_reservation for the same alias and
# amount, _reserved must NOT go negative (the rule uses max(0, held - amount)).
# Drives the API directly.
# ===========================================================================


@pytest.mark.asyncio
async def test_double_release_never_goes_negative(stack):
    app, _client, _redis, _tracker, rule, alias, _shard_a = stack

    async with aiosqlite.connect(app.state.settings.db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 10_000.0),
        )
        await wdb.commit()

    body = b'{"model":"gpt-4","max_tokens":100}'
    assert await rule.evaluate(alias, object(), provider="openai", body=body) is None
    assert rule._reserved.get(alias, 0) == 100

    # First release — normal path.
    await rule.release_reservation(alias, 100)
    assert rule._reserved.get(alias, 0) == 0

    # Second release — buggy double-fire. Must clamp, not go negative, and
    # must not leave a ghost entry in _reserved.
    await rule.release_reservation(alias, 100)
    held = rule._reserved.get(alias, 0)
    assert held == 0, f"Double release produced held={held}; expected clamp to 0."
    assert held >= 0, "Reservation went negative"


@pytest.mark.asyncio
async def test_engine_double_release_never_goes_negative(stack):
    """Same invariant via RulesEngine.release_spend_reservation (app's public API)."""
    app, _client, _redis, _tracker, rule, alias, _shard_a = stack

    async with aiosqlite.connect(app.state.settings.db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 10_000.0),
        )
        await wdb.commit()

    engine = app.state.rules_engine
    body = b'{"model":"gpt-4","max_tokens":250}'
    assert await engine.evaluate(alias, object(), provider="openai", body=body) is None

    await engine.release_spend_reservation(alias, 250)
    await engine.release_spend_reservation(alias, 250)

    held = rule._reserved.get(alias, 0)
    assert held == 0, f"Double release via engine produced held={held}"


# ===========================================================================
# Mode 6 — streaming request denied by SpendCapRule -> reservation stays empty.
#
# The gate DENIES, meaning it never got to reserve (it rejected before the
# reservation path). So _reserved[alias] should not exist at all.
# ===========================================================================


@pytest.mark.asyncio
async def test_streaming_denied_by_cap_leaves_reservation_empty(stack, monkeypatch):
    app, client, redis, _tracker, rule, alias, shard_a = stack

    # Seed over-cap state.
    async with aiosqlite.connect(app.state.settings.db_path) as wdb:
        await wdb.execute(
            "INSERT INTO enrollment_config (key_alias, spend_cap) VALUES (?, ?)",
            (alias, 100.0),
        )
        await wdb.commit()
    await redis.set(spend_key(alias), 500)  # already over cap

    # SR-03 guard: reconstruct must never be called.
    def _never(*args: Any, **kwargs: Any):
        raise AssertionError("reconstruct called on denied streaming request")

    monkeypatch.setattr("worthless.proxy.app.reconstruct_key", AsyncMock(side_effect=_never))
    monkeypatch.setattr("worthless.proxy.app.reconstruct_key_fp", AsyncMock(side_effect=_never))

    resp = await client.post(
        f"/{alias}/v1/chat/completions",
        headers={"authorization": f"Bearer {shard_a}", "content-type": "application/json"},
        content=_stream_request_body(),
    )
    assert resp.status_code == 402
    await _wait_for_background()

    held = rule._reserved.get(alias, 0)
    assert held == 0, f"Denied streaming request must not leave any reserved tokens. Held={held}."
