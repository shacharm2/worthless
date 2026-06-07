"""RED tests for WOR-696 (T7) — fail-closed metering: ceiling table + stream kills.

> Make sure the spend cap actually holds on every request — so once the budget's
> blown, the key stops forming, no matter who's spending or why.

These tests pin down what "T7 done" looks like BEFORE any production code is
written. Each test exercises one specific bypass or kill path. On the current
codebase (epic HEAD = T5 merged, no T7 code yet), the breakdown is:

  - test_estimator_normalizes_max_completion_tokens   → PASSES today (T3 fix)
  - test_zero_reservation_disconnect_uses_ceiling     → FAILS until T7 ships
  - test_unknown_model_rejected_pre_reconstruction    → FAILS until T7 ships
  - test_stream_duration_kill_fires                   → FAILS until T7 ships
  - test_idle_chunk_kill_fires                        → FAILS until T7 ships
  - test_response_model_mismatch_counter_increments   → FAILS until T7 ships
  - test_reconnect_does_not_reset_request_timer       → FAILS until T7 ships

The PASSING test is a regression guard — it proves T3's normalization for
the OpenAI reasoning-model parameter rename still works. The other 6 tests
are the implementation backlog: each one is one slice of T7's AC.

Test stream defaults are deliberately tight (sub-second timeouts) so the
tests are fast. Production defaults (15min / 90s) are operator-tunable;
these tests assert *behavior at the chosen limit*, not the magnitude.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from worthless.crypto.splitter import split_key_fp
from worthless.proxy.app import create_app
from worthless.proxy.config import ProxySettings
from worthless.proxy.estimation import estimate_request_tokens
from worthless.proxy.rules import (
    RateLimitRule,
    RulesEngine,
    SpendCapRule,
    TokenBudgetRule,
)
from worthless.storage.repository import ShardRepository, StoredShard
from worthless.storage.schema import SCHEMA

from tests._fakes import pin_shard_b
from tests._fakes.fake_ipc_supervisor import FakeIPCSupervisor


ALIAS = "wor696-key"
API_KEY = "sk-WOR696-1234567890abcdefghij"
OPENAI_COMPLETIONS = "https://api.openai.com/v1/chat/completions"

# Pinned ceiling values the test asserts T7 fallback uses. These should match
# what eventually lives in src/worthless/proxy/ceilings/model_ceilings.json.
EXPECTED_CEILING_GPT_4O_MINI = 16_384
EXPECTED_CEILING_GPT_5 = 128_000


# ---------------------------------------------------------------------------
# Test 1 — regression guard: estimator already handles max_completion_tokens
# ---------------------------------------------------------------------------


def test_estimator_normalizes_max_completion_tokens() -> None:
    """T3 already handles the OpenAI reasoning-model parameter rename.

    PASSING: this proves _resolve_output_units in estimation.py falls back to
    max_completion_tokens when max_tokens is absent. T7 production code MUST
    keep using this normalization for ceiling lookup; this test fails if
    anyone breaks the fallback in a future refactor.

    This is a regression guard, not a feature test.
    """
    # Reasoning-model shape: only max_completion_tokens, no max_tokens.
    body = json.dumps(
        {
            "model": "o3-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "max_completion_tokens": 500,
        }
    ).encode()

    estimate = estimate_request_tokens(body)

    # Estimator should count the 500 declared output tokens.
    # Conservative lower bound: > 100 (well above the no-max case which
    # would return only input tokens, ~1-2).
    assert estimate > 100, (
        f"estimator returned {estimate} for max_completion_tokens=500 — "
        "T3's normalization is broken; reasoning models will silently "
        "reserve 0 and bypass the cap"
    )


# ---------------------------------------------------------------------------
# Shared fixture helpers for proxy-level tests
# ---------------------------------------------------------------------------


async def _setup_proxy(db_path: str, cap: int = 10_000_000) -> tuple:
    """Bring up the real proxy app + DB + rules engine for a single test.

    Returns: (app, db, rules_engine, shard_a_utf8).
    """
    async with aiosqlite.connect(db_path) as setup:
        await setup.executescript(SCHEMA)
        await setup.execute("PRAGMA journal_mode=WAL")
        await setup.execute("PRAGMA busy_timeout=5000")
        await setup.commit()

    sr = split_key_fp(API_KEY, prefix="sk-", provider="openai")
    fernet_key = Fernet.generate_key()
    repo = ShardRepository(db_path, fernet_key)
    await repo.initialize()
    shard = StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider="openai",
    )
    await repo.store(
        ALIAS,
        shard,
        prefix=sr.prefix,
        charset=sr.charset,
        base_url="https://api.openai.com/v1",
    )

    async with aiosqlite.connect(db_path) as setup:
        await setup.execute(
            "INSERT OR REPLACE INTO enrollment_config "
            "(key_alias, spend_cap, rate_limit_rps) VALUES (?, ?, ?)",
            (ALIAS, cap, 10_000.0),
        )
        await setup.commit()

    settings = ProxySettings(
        db_path=db_path,
        fernet_key=bytearray(fernet_key),
        default_rate_limit_rps=10_000.0,
        upstream_timeout=10.0,
        streaming_timeout=30.0,
        allow_insecure=True,
    )
    app = create_app(settings)
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode=WAL")
    app.state.db = db
    app.state.repo = repo
    app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
    app.state.ipc_supervisor = FakeIPCSupervisor()
    pin_shard_b(app, ALIAS, sr.shard_b)

    db_lock = asyncio.Lock()
    app.state.db_lock = db_lock
    rules_engine = RulesEngine(
        rules=[
            TokenBudgetRule(db=db, lock=db_lock),
            RateLimitRule(default_rps=10_000.0, db_path=db_path),
            SpendCapRule(db=db, lock=db_lock),
        ]
    )
    app.state.rules_engine = rules_engine

    return app, db, rules_engine, sr.shard_a.decode("utf-8")


async def _total_spent(db_path: str) -> int:
    """Sum of tokens in spend_log for ALIAS."""
    async with aiosqlite.connect(db_path) as audit:
        async with audit.execute(
            "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
            (ALIAS,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _pending_count(db_path: str) -> int:
    """Number of rows in pending_charges for ALIAS."""
    async with aiosqlite.connect(db_path) as audit:
        async with audit.execute(
            "SELECT COUNT(*) FROM pending_charges WHERE key_alias = ?",
            (ALIAS,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Test 2 — the 0-reservation leak (THE real T7 fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_reservation_disconnect_uses_ceiling() -> None:
    """No max_tokens, no max_completion_tokens, mid-stream disconnect.

    FAILS today: settle_at_estimate writes 0 because the original reservation
    was 0. The cap never fires.

    Passes when T7 ships: settle_at_estimate consults the ceiling table for
    (provider, model) and charges the model's documented max output tokens
    instead of 0.
    """
    with tempfile.TemporaryDirectory(prefix="wor696-zeroreserv-") as tmp:
        db_path = str(Path(tmp) / "proxy.db")
        app, db, _rules, shard_a_utf8 = await _setup_proxy(db_path)
        transport = httpx.ASGITransport(app=app)

        # Body has NEITHER max_tokens NOR max_completion_tokens → estimator
        # returns 0 output reservation today.
        body = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }
        ).encode()

        # SSE stream with NO usage block → the BG task hits
        # settle_at_estimate, not settle_spend. Today that writes 0.
        sse = (
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

        try:
            with respx.mock(assert_all_called=False) as router:
                router.post(OPENAI_COMPLETIONS).mock(
                    return_value=httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        stream=httpx.ByteStream(sse),
                    )
                )

                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        f"/{ALIAS}/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {shard_a_utf8}",
                            "content-type": "application/json",
                        },
                        content=body,
                    )
                    if response.status_code == 200:
                        await response.aread()
                    assert response.status_code == 200, (
                        f"setup: expected 200, got {response.status_code}"
                    )

                await asyncio.sleep(0.05)  # let BG task land

            spent = await _total_spent(db_path)
            pending = await _pending_count(db_path)

            # The T7 assertion: total_spent must move by at least the
            # ceiling for gpt-4o-mini (16384), NOT 0. Without T7, this
            # fails because settle_at_estimate uses the 0 reservation.
            assert spent >= EXPECTED_CEILING_GPT_4O_MINI, (
                f"0-reservation leak: total_spent={spent}, "
                f"expected ≥ {EXPECTED_CEILING_GPT_4O_MINI} "
                f"(gpt-4o-mini ceiling). settle_at_estimate wrote the "
                f"original 0 reservation instead of the ceiling fallback."
            )
            assert pending == 0, (
                f"pending_charges still holds {pending} row(s) — hold was never consumed"
            )

        finally:
            await app.state.httpx_client.aclose()
            await db.close()


# ---------------------------------------------------------------------------
# Test 3 — unknown model fail-closed reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_model_rejected_pre_reconstruction() -> None:
    """Request to a model not in the ceiling table → reject before reconstruct.

    FAILS today: the proxy admits any model name and the request reaches
    reconstruction. Cap silently doesn't engage because no ceiling lookup
    happens (no ceiling code yet).

    Passes when T7 ships: admission resolves the (provider, model) ceiling
    BEFORE the rules engine reserves; an unknown model triggers a 4xx with a
    clear error code (WRTLS-150 or similar). Zero spend_log rows. Zero
    pending_charges rows. Upstream never called.
    """
    with tempfile.TemporaryDirectory(prefix="wor696-unkmodel-") as tmp:
        db_path = str(Path(tmp) / "proxy.db")
        app, db, _rules, shard_a_utf8 = await _setup_proxy(db_path)
        transport = httpx.ASGITransport(app=app)

        body = json.dumps(
            {
                "model": "made-up-model-that-does-not-exist-xyz",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 50,
            }
        ).encode()

        try:
            with respx.mock(assert_all_called=False) as router:
                # If the proxy somehow lets the request through, respx will
                # 200 it — but the test asserts the proxy rejects BEFORE
                # this mock is called.
                route = router.post(OPENAI_COMPLETIONS).mock(
                    return_value=httpx.Response(200, json={"choices": []})
                )

                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        f"/{ALIAS}/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {shard_a_utf8}",
                            "content-type": "application/json",
                        },
                        content=body,
                    )

                # T7 assertion: must reject with 4xx before upstream is hit.
                assert 400 <= response.status_code < 500, (
                    f"unknown-model leak: expected 4xx reject, got "
                    f"{response.status_code}. Body: {response.text[:200]}"
                )

                # Upstream MUST NOT have been called — reject happens
                # before key reconstruction, before HTTP egress.
                assert route.call_count == 0, (
                    f"unknown-model leak: upstream was called "
                    f"{route.call_count} times. T7 must reject BEFORE "
                    f"reconstruct so the key never reassembles for an "
                    f"unknown model."
                )

            # No spend_log, no pending_charges for a rejected request.
            spent = await _total_spent(db_path)
            pending = await _pending_count(db_path)
            assert spent == 0, f"unknown-model request created spend_log entry: {spent} tokens"
            assert pending == 0, f"unknown-model request left pending_charges: {pending} row(s)"

        finally:
            await app.state.httpx_client.aclose()
            await db.close()


# ---------------------------------------------------------------------------
# Test 4 — total stream-duration kill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_duration_kill_fires() -> None:
    """A stream that never ends is killed at max_stream_duration_seconds.

    FAILS today: no duration kill exists; the proxy will stream until
    upstream closes or the underlying httpx timeout fires. Even when the
    httpx timeout fires, settle_at_estimate writes 0 (no max_tokens), so
    the cap doesn't move.

    Passes when T7 ships: stream forwarder enforces a hard wall-clock
    duration cut. When the cut fires, settle_at_estimate uses the model
    ceiling (NOT 0) so the counter moves by ≥ ceiling.

    Test uses a 0.5s duration limit so the test is fast; production
    default is 15min, operator-tunable.
    """
    with tempfile.TemporaryDirectory(prefix="wor696-dur-kill-") as tmp:
        db_path = str(Path(tmp) / "proxy.db")
        app, db, _rules, shard_a_utf8 = await _setup_proxy(db_path)
        # T7 will introduce these settings on app.state or ProxySettings.
        # The test sets them directly so we don't have to wait 15 minutes.
        app.state.max_stream_duration_seconds = 0.5
        transport = httpx.ASGITransport(app=app)

        body = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }
        ).encode()

        async def _never_ending_stream() -> Any:
            # Stream for ~1.5s — longer than the 0.5s duration limit but
            # bounded so the test terminates cleanly when T7 isn't shipped
            # yet. T7's kill should fire well before this naturally ends.
            for _ in range(30):
                yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                await asyncio.sleep(0.05)

        try:
            with respx.mock(assert_all_called=False) as router:
                router.post(OPENAI_COMPLETIONS).mock(
                    return_value=httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        stream=_never_ending_stream(),
                    )
                )

                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        f"/{ALIAS}/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {shard_a_utf8}",
                            "content-type": "application/json",
                        },
                        content=body,
                        timeout=5.0,
                    )
                    if response.status_code == 200:
                        try:
                            await response.aread()
                        except (httpx.ReadError, httpx.RemoteProtocolError):
                            # Expected: T7's kill closes the upstream
                            # stream, which surfaces as a read error.
                            pass

                await asyncio.sleep(0.1)

            spent = await _total_spent(db_path)
            assert spent >= EXPECTED_CEILING_GPT_4O_MINI, (
                f"stream-duration kill: total_spent={spent}, expected ≥ "
                f"{EXPECTED_CEILING_GPT_4O_MINI} (ceiling). Either the "
                f"kill never fired, or settle_at_estimate wrote 0 instead "
                f"of the ceiling."
            )

        finally:
            await app.state.httpx_client.aclose()
            await db.close()


# ---------------------------------------------------------------------------
# Test 5 — idle-chunk timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_chunk_kill_fires() -> None:
    """A stream silent for max_idle_between_chunks_seconds is killed.

    FAILS today: no idle-chunk timeout. A stream can pause forever between
    chunks and the proxy will keep waiting.

    Passes when T7 ships: idle timer between SSE chunks; reset on each
    chunk arrival; fires if the gap exceeds the threshold; settle at
    ceiling. Test uses a 0.3s idle limit.
    """
    with tempfile.TemporaryDirectory(prefix="wor696-idle-kill-") as tmp:
        db_path = str(Path(tmp) / "proxy.db")
        app, db, _rules, shard_a_utf8 = await _setup_proxy(db_path)
        app.state.max_idle_between_chunks_seconds = 0.3
        transport = httpx.ASGITransport(app=app)

        body = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }
        ).encode()

        async def _slow_drip_stream() -> Any:
            # First chunk arrives quickly, then a 1-second gap (well over
            # the 0.3s idle limit T7 should enforce). Bounded so the test
            # terminates cleanly when T7 isn't shipped.
            yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
            await asyncio.sleep(1.0)
            yield b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n'

        try:
            with respx.mock(assert_all_called=False) as router:
                router.post(OPENAI_COMPLETIONS).mock(
                    return_value=httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        stream=_slow_drip_stream(),
                    )
                )

                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        f"/{ALIAS}/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {shard_a_utf8}",
                            "content-type": "application/json",
                        },
                        content=body,
                        timeout=5.0,
                    )
                    if response.status_code == 200:
                        try:
                            await response.aread()
                        except (httpx.ReadError, httpx.RemoteProtocolError):
                            pass

                await asyncio.sleep(0.1)

            spent = await _total_spent(db_path)
            assert spent >= EXPECTED_CEILING_GPT_4O_MINI, (
                f"idle-chunk kill: total_spent={spent}, expected ≥ "
                f"{EXPECTED_CEILING_GPT_4O_MINI} (ceiling). Either the "
                f"idle kill never fired, or settle wrote 0."
            )

        finally:
            await app.state.httpx_client.aclose()
            await db.close()


# ---------------------------------------------------------------------------
# Test 6 — response-model swap tripwire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_model_mismatch_counter_increments() -> None:
    """Request a cheap model, get a different (expensive) model in response.

    FAILS today: the proxy doesn't extract response.model from SSE chunks.
    No mismatch detection. If a provider silently routes a gpt-4o-mini
    request to gpt-5, the user is billed at mini prices.

    Passes when T7 ships: response.model extracted per-chunk (OpenAI) or
    from message_start (Anthropic); compared to request.model; counter
    increments on mismatch; effective ceiling adjusted upward.

    Counter shape: worthless_response_model_mismatch_total{request_model,
    response_model}.
    """
    with tempfile.TemporaryDirectory(prefix="wor696-mismatch-") as tmp:
        db_path = str(Path(tmp) / "proxy.db")
        app, db, _rules, shard_a_utf8 = await _setup_proxy(db_path)
        transport = httpx.ASGITransport(app=app)

        body = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 50,
                "stream": True,
            }
        ).encode()

        # Response SSE echoes a DIFFERENT model than the request.
        sse = (
            b'data: {"model":"gpt-5","choices":[{"delta":{"role":"assistant"}}]}\n\n'
            b'data: {"model":"gpt-5","choices":[{"delta":{"content":"hi"}}]}\n\n'
            b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
            b"data: [DONE]\n\n"
        )

        try:
            with respx.mock(assert_all_called=False) as router:
                router.post(OPENAI_COMPLETIONS).mock(
                    return_value=httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        stream=httpx.ByteStream(sse),
                    )
                )

                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post(
                        f"/{ALIAS}/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {shard_a_utf8}",
                            "content-type": "application/json",
                        },
                        content=body,
                    )
                    if response.status_code == 200:
                        await response.aread()
                    assert response.status_code == 200, (
                        f"setup: expected 200, got {response.status_code}"
                    )

                await asyncio.sleep(0.05)

            # T7 will expose the counter via app.state or a module-level
            # registry. This assertion shape is intentionally flexible —
            # the implementer can decide between Prometheus client, a
            # simple dict on app.state, or an OTel counter. Whatever it
            # is, the test will assert mismatch was observed.
            counter_value = getattr(app.state, "response_model_mismatch_counter", None)
            assert counter_value is not None, (
                "T7 must expose a response-model mismatch counter on "
                "app.state.response_model_mismatch_counter (or equivalent). "
                "Today no counter exists."
            )
            mismatch_count = counter_value.get(("gpt-4o-mini", "gpt-5"), 0)
            assert mismatch_count >= 1, (
                f"expected mismatch counter for (gpt-4o-mini, gpt-5) ≥ 1, "
                f"got {mismatch_count}. T7 must extract response.model "
                f"from SSE and compare to request.model."
            )

        finally:
            await app.state.httpx_client.aclose()
            await db.close()


# ---------------------------------------------------------------------------
# Test 7 — reconnect does not reset the per-logical-request timer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_does_not_reset_request_timer() -> None:
    """A client reconnect with the same request_id keeps the timer counting.

    FAILS today: no per-logical-request timer exists; every new HTTP
    connection creates a new pending_charges hold with its own timer.
    Attacker could chain reconnects to extend total streaming wall time
    beyond max_stream_duration.

    Passes when T7 ships: if a request carries an X-Request-Id header (or
    similar), the duration timer is keyed by that ID and survives
    individual socket reconnects. Attacker can't dodge the duration cut.

    This test is the strictest of the 7 because it requires T7 to
    introduce a per-logical-request concept that doesn't exist yet. If
    the operator decides X-Request-Id is overkill for v1, this test can
    be deferred — but the design must explicitly say so.
    """
    with tempfile.TemporaryDirectory(prefix="wor696-reconnect-") as tmp:
        db_path = str(Path(tmp) / "proxy.db")
        app, db, _rules, shard_a_utf8 = await _setup_proxy(db_path)
        app.state.max_stream_duration_seconds = 0.5
        transport = httpx.ASGITransport(app=app)

        body = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }
        ).encode()

        # Same X-Request-Id for both "reconnect" attempts.
        request_id = "logical-req-abc-123"

        async def _half_stream() -> Any:
            # Stream a few chunks then "disconnect" (generator ends).
            for _ in range(3):
                yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                await asyncio.sleep(0.1)

        try:
            with respx.mock(assert_all_called=False) as router:
                router.post(OPENAI_COMPLETIONS).mock(
                    return_value=httpx.Response(
                        200,
                        headers={"content-type": "text/event-stream"},
                        stream=_half_stream(),
                    )
                )

                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    # First connection — runs for ~0.3s.
                    r1 = await client.post(
                        f"/{ALIAS}/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {shard_a_utf8}",
                            "content-type": "application/json",
                            "x-request-id": request_id,
                        },
                        content=body,
                        timeout=5.0,
                    )
                    if r1.status_code == 200:
                        try:
                            await r1.aread()
                        except (httpx.ReadError, httpx.RemoteProtocolError):
                            pass

                    # "Reconnect" — same X-Request-Id, fresh socket.
                    # Logical timer is at ~0.3s of 0.5s budget. After
                    # ~0.3s more of streaming, the timer must fire.
                    r2 = await client.post(
                        f"/{ALIAS}/v1/chat/completions",
                        headers={
                            "authorization": f"Bearer {shard_a_utf8}",
                            "content-type": "application/json",
                            "x-request-id": request_id,
                        },
                        content=body,
                        timeout=5.0,
                    )
                    if r2.status_code == 200:
                        try:
                            await r2.aread()
                        except (httpx.ReadError, httpx.RemoteProtocolError):
                            pass

                await asyncio.sleep(0.1)

            # The logical-request timer should have fired during r2 (or
            # at r2 admission if cumulative > 0.5s). Either way, settle
            # at ceiling at least once means total_spent ≥ ceiling.
            spent = await _total_spent(db_path)
            assert spent >= EXPECTED_CEILING_GPT_4O_MINI, (
                f"reconnect bypassed the duration timer: total_spent="
                f"{spent}, expected ≥ {EXPECTED_CEILING_GPT_4O_MINI} "
                f"(ceiling fallback should have fired on at least one "
                f"of the two requests). T7 must key the duration timer "
                f"by X-Request-Id, not per-socket."
            )

        finally:
            await app.state.httpx_client.aclose()
            await db.close()
