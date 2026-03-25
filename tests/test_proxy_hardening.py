"""Proxy hardening tests — repr redaction, dead code removal, SSE streaming,
gate ordering, zeroing, error handling.

Tests for Phase 3.1:
- Plan 01: AdapterRequest/Response repr redaction, dead code removal, bytearray compliance
- Plan 02: SSE streaming, gate-before-decrypt, zeroing, async I/O, error handling,
  upstream sanitization, anti-enumeration, metering resilience
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

import aiosqlite

from worthless.adapters import registry
from worthless.adapters.types import AdapterRequest, AdapterResponse
from worthless.crypto import split_key
from worthless.proxy.app import create_app
from worthless.proxy.config import ProxySettings
from worthless.proxy.errors import ErrorResponse, gateway_error_response
from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule
from worthless.storage.repository import EncryptedShard, StoredShard


# ------------------------------------------------------------------
# Fixtures (Plan 02)
# ------------------------------------------------------------------


@pytest.fixture()
def proxy_settings(tmp_db_path: str, fernet_key: bytes, tmp_path) -> ProxySettings:
    shard_a_dir = str(tmp_path / "shard_a")
    return ProxySettings(
        db_path=tmp_db_path,
        fernet_key=fernet_key.decode(),
        default_rate_limit_rps=100.0,
        upstream_timeout=10.0,
        streaming_timeout=30.0,
        allow_insecure=True,
        shard_a_dir=shard_a_dir,
    )


@pytest.fixture()
async def enrolled_alias(repo, proxy_settings: ProxySettings, sample_api_key_bytes: bytes):
    """Enroll a test key and return (alias, shard_a_b64, raw_api_key)."""
    alias = "test-key"
    sr = split_key(sample_api_key_bytes)

    shard = StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider="openai",
    )
    await repo.store(alias, shard)

    shard_a_dir = proxy_settings.shard_a_dir
    os.makedirs(shard_a_dir, exist_ok=True)
    shard_a_path = os.path.join(shard_a_dir, alias)
    with open(shard_a_path, "wb") as f:
        f.write(bytes(sr.shard_a))

    shard_a_b64 = base64.b64encode(bytes(sr.shard_a)).decode()
    return alias, shard_a_b64, sample_api_key_bytes


@pytest.fixture()
async def proxy_app(proxy_settings: ProxySettings, repo):
    app = create_app(proxy_settings)
    db = await aiosqlite.connect(proxy_settings.db_path)
    app.state.db = db
    app.state.repo = repo
    app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
    app.state.rules_engine = RulesEngine(
        rules=[
            SpendCapRule(db=db),
            RateLimitRule(default_rps=proxy_settings.default_rate_limit_rps),
        ]
    )
    yield app
    await app.state.httpx_client.aclose()
    await db.close()


@pytest.fixture()
async def proxy_client(proxy_app):
    transport = httpx.ASGITransport(app=proxy_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ==================================================================
# Plan 01 tests (repr redaction, dead code removal, bytearray)
# ==================================================================


# ------------------------------------------------------------------
# AdapterRequest repr redaction (SR-04)
# ------------------------------------------------------------------


class TestAdapterRequestRepr:
    """AdapterRequest.__repr__ must not expose body content."""

    def test_body_redacted_in_repr(self) -> None:
        req = AdapterRequest(
            url="https://api.openai.com/v1/chat/completions",
            headers={"content-type": "application/json"},
            body=b'{"model":"gpt-4","messages":[{"role":"user","content":"secret prompt"}]}',
        )
        r = repr(req)
        assert "secret prompt" not in r
        assert "<" in r and "bytes>" in r

    def test_body_length_shown(self) -> None:
        body = b"x" * 42
        req = AdapterRequest(url="https://example.com", headers={}, body=body)
        assert "<42 bytes>" in repr(req)

    def test_sensitive_headers_still_redacted(self) -> None:
        req = AdapterRequest(
            url="https://example.com",
            headers={"authorization": "Bearer sk-secret-key"},
            body=b"{}",
        )
        r = repr(req)
        assert "sk-secret-key" not in r
        assert "REDACTED" in r


# ------------------------------------------------------------------
# AdapterResponse repr redaction (SR-04)
# ------------------------------------------------------------------


class TestAdapterResponseRepr:
    """AdapterResponse.__repr__ must not expose body or header values."""

    def test_body_redacted_in_repr(self) -> None:
        resp = AdapterResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"choices":[{"message":{"content":"secret response"}}]}',
        )
        r = repr(resp)
        assert "secret response" not in r
        assert "<" in r and "bytes>" in r

    def test_headers_redacted_in_repr(self) -> None:
        resp = AdapterResponse(
            status_code=200,
            headers={"x-request-id": "abc123", "content-type": "application/json"},
            body=b"{}",
        )
        r = repr(resp)
        assert "abc123" not in r
        assert "<2 entries>" in r

    def test_body_length_shown(self) -> None:
        resp = AdapterResponse(status_code=200, headers={}, body=b"y" * 99)
        assert "<99 bytes>" in repr(resp)


# ------------------------------------------------------------------
# Dead code removal
# ------------------------------------------------------------------


class TestDeadCodeRemoval:
    def test_dependencies_module_removed(self) -> None:
        dep_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "worthless"
            / "proxy"
            / "dependencies.py"
        )
        assert not dep_path.exists()

    def test_dependencies_module_not_importable(self) -> None:
        with pytest.raises(ImportError):
            import worthless.proxy.dependencies  # noqa: F401


# ------------------------------------------------------------------
# Bytearray compliance (SR-01)
# ------------------------------------------------------------------


class TestBytearrayCompliance:
    def test_stored_shard_bytearray_fields(self) -> None:
        shard = StoredShard(
            shard_b=bytearray(b"shard-b-data"),
            commitment=bytearray(b"commitment-data"),
            nonce=bytearray(b"nonce-data"),
            provider="openai",
        )
        assert isinstance(shard.shard_b, bytearray)
        assert isinstance(shard.commitment, bytearray)
        assert isinstance(shard.nonce, bytearray)


class TestStoredShardRepr:
    """StoredShard.__repr__ must not expose shard material (SR-04)."""

    def test_shard_b_not_in_repr(self) -> None:
        shard = StoredShard(
            shard_b=bytearray(b"secret-shard-b"),
            commitment=bytearray(b"secret-commitment"),
            nonce=bytearray(b"secret-nonce"),
            provider="openai",
        )
        r = repr(shard)
        assert b"secret-shard-b".decode() not in r
        assert b"secret-commitment".decode() not in r
        assert b"secret-nonce".decode() not in r
        assert "<14 bytes>" in r
        assert "provider='openai'" in r

    def test_encrypted_shard_repr_redacted(self) -> None:
        enc = EncryptedShard(
            shard_b_enc=b"encrypted-data-here",
            commitment=b"commit-bytes",
            nonce=b"nonce-bytes",
            provider="anthropic",
        )
        r = repr(enc)
        assert b"encrypted-data-here".decode() not in r
        assert "<19 bytes>" in r
        assert "provider='anthropic'" in r


# ==================================================================
# Plan 02 tests (SSE streaming, gate ordering, zeroing, errors, etc.)
# ==================================================================


# ------------------------------------------------------------------
# B-1: SSE Streaming
# ------------------------------------------------------------------


class TestSSEStreaming:
    @respx.mock
    async def test_stream_true_passed_to_httpx_send(self, proxy_app, enrolled_alias):
        """Verify httpx.send() is called with stream=True for all requests."""
        alias, shard_a_b64, _ = enrolled_alias
        send_kwargs: dict = {}

        async def capturing_send(req, **kwargs):
            send_kwargs.update(kwargs)
            # Return a non-streaming response to keep things simple
            return httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 5}},
                request=req,
            )

        proxy_app.state.httpx_client.send = capturing_send

        respx.post("https://api.openai.com/v1/chat/completions").pass_through()

        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/chat/completions",
                headers={
                    "x-worthless-alias": alias,
                    "x-worthless-shard-a": shard_a_b64,
                    "content-type": "application/json",
                },
                content=b'{"model": "gpt-4", "messages": []}',
            )

        assert send_kwargs.get("stream") is True, "httpx.send() must be called with stream=True"

    @respx.mock
    async def test_streaming_response_uses_streaming_path(self, proxy_app, enrolled_alias):
        """When adapter returns is_streaming=True, proxy uses StreamingResponse."""
        alias, shard_a_b64, _ = enrolled_alias
        sse_body = b'data: {"id":"1","choices":[{"delta":{"content":"Hello"}}]}\n\ndata: [DONE]\n\n'

        async def sse_stream():
            for chunk in sse_body.split(b"\n\n"):
                if chunk:
                    yield chunk + b"\n\n"

        # Mock the adapter to return a streaming response
        async def mock_relay(upstream_resp):
            return AdapterResponse(
                status_code=200,
                headers={"content-type": "text/event-stream"},
                body=b"",
                is_streaming=True,
                stream=sse_stream(),
            )

        # Mock httpx send to return a streaming-like response
        async def mock_send(req, **kwargs):
            resp = httpx.Response(200, headers={"content-type": "text/event-stream"}, request=req)
            return resp

        proxy_app.state.httpx_client.send = mock_send

        # Patch the adapter's relay_response
        adapter = registry.get_adapter("/v1/chat/completions")
        orig_relay = adapter.relay_response
        adapter.relay_response = mock_relay

        try:
            transport = httpx.ASGITransport(app=proxy_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={
                        "x-worthless-alias": alias,
                        "x-worthless-shard-a": shard_a_b64,
                        "content-type": "application/json",
                    },
                    content=b'{"model": "gpt-4", "messages": [], "stream": true}',
                )
            assert resp.status_code == 200
            assert b"Hello" in resp.content
        finally:
            if orig_relay:
                adapter.relay_response = orig_relay

    @respx.mock
    async def test_non_streaming_response_properly_handled(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Non-streaming responses are read and returned normally."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 10}},
            )
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 200
        assert b"hi" in resp.content


# ------------------------------------------------------------------
# B-2: Gate-before-decrypt ordering
# ------------------------------------------------------------------


class TestGateBeforeDecrypt:
    @respx.mock
    async def test_fetch_encrypted_before_rules_decrypt_after(self, proxy_app, enrolled_alias):
        """fetch_encrypted called BEFORE rules_engine.evaluate, decrypt_shard AFTER."""
        alias, shard_a_b64, _ = enrolled_alias
        call_order: list[str] = []

        orig_fetch = proxy_app.state.repo.fetch_encrypted
        orig_decrypt = proxy_app.state.repo.decrypt_shard

        async def mock_fetch(a):
            call_order.append("fetch_encrypted")
            return await orig_fetch(a)

        def mock_decrypt(enc):
            call_order.append("decrypt_shard")
            return orig_decrypt(enc)

        async def mock_evaluate(_self, a, r, **kwargs):
            call_order.append("evaluate")
            return None

        proxy_app.state.repo.fetch_encrypted = mock_fetch
        proxy_app.state.repo.decrypt_shard = mock_decrypt
        proxy_app.state.rules_engine = type("MockEngine", (), {"evaluate": mock_evaluate})()

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 5}},
            )
        )

        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/chat/completions",
                headers={
                    "x-worthless-alias": alias,
                    "x-worthless-shard-a": shard_a_b64,
                    "content-type": "application/json",
                },
                content=b'{"model": "gpt-4", "messages": []}',
            )

        assert call_order == ["fetch_encrypted", "evaluate", "decrypt_shard"]

    @respx.mock
    async def test_denial_skips_decrypt(self, proxy_app, enrolled_alias):
        """When rules engine denies, decrypt_shard is never called."""
        alias, shard_a_b64, _ = enrolled_alias
        decrypt_called = False

        orig_decrypt = proxy_app.state.repo.decrypt_shard

        def mock_decrypt(enc):
            nonlocal decrypt_called
            decrypt_called = True
            return orig_decrypt(enc)

        proxy_app.state.repo.decrypt_shard = mock_decrypt
        proxy_app.state.rules_engine = type(
            "MockEngine",
            (),
            {
                "evaluate": AsyncMock(
                    return_value=ErrorResponse(
                        status_code=402,
                        body=b'{"error": "spend cap exceeded"}',
                        headers={"content-type": "application/json"},
                    )
                )
            },
        )()

        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                headers={
                    "x-worthless-alias": alias,
                    "x-worthless-shard-a": shard_a_b64,
                },
                content=b'{"model": "gpt-4", "messages": []}',
            )
        assert resp.status_code == 402
        assert not decrypt_called


# ------------------------------------------------------------------
# B-3: Bytearray zeroing
# ------------------------------------------------------------------


class TestByteArrayZeroing:
    @respx.mock
    async def test_shard_material_zeroed_after_request(self, proxy_app, enrolled_alias):
        """shard_a and stored shard fields are zeroed after request completes."""
        alias, shard_a_b64, _ = enrolled_alias
        captured_stored: dict = {}

        orig_decrypt = proxy_app.state.repo.decrypt_shard

        def capturing_decrypt(enc):
            result = orig_decrypt(enc)
            captured_stored["shard"] = result
            return result

        proxy_app.state.repo.decrypt_shard = capturing_decrypt

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 5}},
            )
        )

        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/chat/completions",
                headers={
                    "x-worthless-alias": alias,
                    "x-worthless-shard-a": shard_a_b64,
                    "content-type": "application/json",
                },
                content=b'{"model": "gpt-4", "messages": []}',
            )

        shard = captured_stored["shard"]
        assert all(b == 0 for b in shard.shard_b), "shard_b not zeroed"
        assert all(b == 0 for b in shard.commitment), "commitment not zeroed"
        assert all(b == 0 for b in shard.nonce), "nonce not zeroed"


# ------------------------------------------------------------------
# Reconstruct failure zeroing
# ------------------------------------------------------------------


class TestReconstructFailureZeroing:
    @respx.mock
    async def test_shard_material_zeroed_on_reconstruct_failure(self, proxy_app, enrolled_alias):
        """When reconstruct_key raises, all shard material is zeroed and 401 returned."""
        alias, shard_a_b64, _ = enrolled_alias
        captured_stored: dict = {}

        orig_decrypt = proxy_app.state.repo.decrypt_shard

        def capturing_decrypt(enc):
            result = orig_decrypt(enc)
            captured_stored["shard"] = result
            return result

        proxy_app.state.repo.decrypt_shard = capturing_decrypt

        with patch(
            "worthless.proxy.app.reconstruct_key",
            side_effect=Exception("tampered shard"),
        ):
            transport = httpx.ASGITransport(app=proxy_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={
                        "x-worthless-alias": alias,
                        "x-worthless-shard-a": shard_a_b64,
                        "content-type": "application/json",
                    },
                    content=b'{"model": "gpt-4", "messages": []}',
                )

        assert resp.status_code == 401
        shard = captured_stored["shard"]
        assert all(b == 0 for b in shard.shard_b), "shard_b not zeroed on failure"
        assert all(b == 0 for b in shard.commitment), "commitment not zeroed on failure"
        assert all(b == 0 for b in shard.nonce), "nonce not zeroed on failure"


# ------------------------------------------------------------------
# B-4: Async file I/O
# ------------------------------------------------------------------


class TestAsyncFileIO:
    @respx.mock
    async def test_file_shard_a_uses_to_thread(self, proxy_app, enrolled_alias):
        """File-based shard_a loading uses asyncio.to_thread."""
        alias, _, _ = enrolled_alias
        to_thread_called = False

        orig_to_thread = asyncio.to_thread

        async def mock_to_thread(func, *args, **kwargs):
            nonlocal to_thread_called
            to_thread_called = True
            return await orig_to_thread(func, *args, **kwargs)

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 5}},
            )
        )

        with patch("worthless.proxy.app.asyncio") as mock_asyncio_mod:
            mock_asyncio_mod.to_thread = mock_to_thread
            # Keep create_task working
            mock_asyncio_mod.create_task = asyncio.create_task

            transport = httpx.ASGITransport(app=proxy_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={
                        "x-worthless-alias": alias,
                        "content-type": "application/json",
                    },
                    content=b'{"model": "gpt-4", "messages": []}',
                )
            assert resp.status_code == 200
            assert to_thread_called


# ------------------------------------------------------------------
# H-1: Error handling (502/504)
# ------------------------------------------------------------------


class TestErrorHandling:
    @respx.mock
    async def test_timeout_returns_504(self, proxy_client: httpx.AsyncClient, enrolled_alias):
        """httpx.TimeoutException returns 504."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 504

    @respx.mock
    async def test_connect_error_returns_502(self, proxy_client: httpx.AsyncClient, enrolled_alias):
        """httpx.ConnectError returns 502."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 502

    @respx.mock
    async def test_generic_http_error_returns_502(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Generic httpx.HTTPError (not Timeout/Connect) returns 502."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=httpx.HTTPError("some http error")
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 502


# ------------------------------------------------------------------
# Invalid shard_a header
# ------------------------------------------------------------------


class TestInvalidShardA:
    async def test_invalid_base64_shard_a_returns_401(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Invalid base64 in x-worthless-shard-a header returns uniform 401."""
        alias, _, _ = enrolled_alias

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": "!!!not-valid-base64!!!",
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 401


# ------------------------------------------------------------------
# M-4: Upstream error sanitization
# ------------------------------------------------------------------


class TestUpstreamSanitization:
    @respx.mock
    async def test_upstream_error_body_sanitized(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Upstream 4xx/5xx error bodies are sanitized."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                500,
                json={
                    "error": {
                        "message": "Internal server details leaked",
                        "type": "server_error",
                    }
                },
            )
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert b"Internal server details leaked" not in resp.content
        assert resp.status_code == 500

    @respx.mock
    async def test_malformed_upstream_error_uses_fallback(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Malformed upstream error body falls back to generic error."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                500,
                content=b"not valid json at all",
                headers={"content-type": "text/plain"},
            )
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 500
        body = json.loads(resp.content)
        assert body["error"]["message"] == "upstream provider error"


class TestUpstreamSanitizationAnthropic:
    @respx.mock
    async def test_anthropic_error_body_sanitized(
        self, proxy_app, tmp_path, fernet_key, sample_api_key_bytes
    ):
        """Upstream Anthropic error bodies are sanitized to Anthropic format."""
        # Enroll an Anthropic key
        alias = "anthropic-key"
        sr = split_key(sample_api_key_bytes)
        shard = StoredShard(
            shard_b=bytearray(sr.shard_b),
            commitment=bytearray(sr.commitment),
            nonce=bytearray(sr.nonce),
            provider="anthropic",
        )
        await proxy_app.state.repo.store(alias, shard)
        shard_a_b64 = base64.b64encode(bytes(sr.shard_a)).decode()

        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                500,
                json={
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": "Internal server secret details leaked here",
                    },
                },
            )
        )

        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/messages",
                headers={
                    "x-worthless-alias": alias,
                    "x-worthless-shard-a": shard_a_b64,
                    "content-type": "application/json",
                },
                content=b'{"model": "claude-3-5-sonnet-20241022",'
                b' "max_tokens": 10, "messages": []}',
            )

        body = json.loads(resp.content)
        assert resp.status_code == 500
        assert b"Internal server secret details leaked here" not in resp.content
        assert body["type"] == "error"
        assert body["error"]["type"] == "api_error"
        assert body["error"]["message"] == "upstream provider error"


# ------------------------------------------------------------------
# H-2/M-3: Anti-enumeration
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Path normalization
# ------------------------------------------------------------------


class TestPathNormalization:
    """Verify path cleaning handles edge cases without breaking routing."""

    @respx.mock
    async def test_path_with_query_params_stripped(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Query params are stripped — /v1/chat/completions?foo=bar routes correctly."""
        alias, shard_a_b64, _ = enrolled_alias
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 1}},
            )
        )
        resp = await proxy_client.post(
            "/v1/chat/completions?foo=bar",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 200

    @respx.mock
    async def test_path_with_multiple_query_separators(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Multiple ? in path — only first segment used."""
        alias, shard_a_b64, _ = enrolled_alias
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 1}},
            )
        )
        resp = await proxy_client.post(
            "/v1/chat/completions?a=1?b=2",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 200

    async def test_unknown_path_returns_401(self, proxy_client: httpx.AsyncClient, enrolled_alias):
        """Unrecognized path returns uniform 401 (not 404)."""
        alias, shard_a_b64, _ = enrolled_alias
        resp = await proxy_client.post(
            "/v2/something/else",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
            },
            content=b"{}",
        )
        assert resp.status_code == 401

    async def test_empty_path_returns_401(self, proxy_client: httpx.AsyncClient, enrolled_alias):
        """Root path / returns uniform 401 (no adapter matches)."""
        alias, shard_a_b64, _ = enrolled_alias
        resp = await proxy_client.post(
            "/",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
            },
            content=b"{}",
        )
        assert resp.status_code == 401


class TestAntiEnumeration:
    async def test_unknown_endpoint_returns_401_not_404(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Unknown endpoint returns 401 format, not 404."""
        alias, shard_a_b64, _ = enrolled_alias
        resp = await proxy_client.post(
            "/v1/unknown",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
            },
            content=b"{}",
        )
        assert resp.status_code == 401

    async def test_all_failure_responses_identical_format(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """404 (unknown endpoint) and 401 (no alias) return same body."""
        alias, shard_a_b64, _ = enrolled_alias
        r1 = await proxy_client.post("/v1/chat/completions", content=b"{}")
        r2 = await proxy_client.post(
            "/v1/unknown",
            headers={
                "x-worthless-alias": alias,
                "x-worthless-shard-a": shard_a_b64,
            },
            content=b"{}",
        )
        assert r1.status_code == r2.status_code == 401
        assert r1.content == r2.content


# ------------------------------------------------------------------
# M-9/M-10: Metering resilience
# ------------------------------------------------------------------


class TestMeteringResilience:
    @respx.mock
    async def test_record_spend_failure_does_not_break_response(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """record_spend failure logs warning but does not break response."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 10}},
            )
        )

        with patch("worthless.proxy.app.record_spend", side_effect=Exception("db error")):
            resp = await proxy_client.post(
                "/v1/chat/completions",
                headers={
                    "x-worthless-alias": alias,
                    "x-worthless-shard-a": shard_a_b64,
                    "content-type": "application/json",
                },
                content=b'{"model": "gpt-4", "messages": []}',
            )
        assert resp.status_code == 200


# ------------------------------------------------------------------
# Gateway error response structure
# ------------------------------------------------------------------


class TestGatewayErrorResponse:
    def test_gateway_error_response_structure(self):
        """gateway_error_response produces correct JSON structure."""
        err = gateway_error_response(502, "bad gateway")
        assert err.status_code == 502
        assert b"bad gateway" in err.body
        assert err.headers["content-type"] == "application/json"


# ==================================================================
# Plan 03 tests (body size limit, CORS denial)
# ==================================================================


# ------------------------------------------------------------------
# M-1: Body Size Limit Middleware
# ------------------------------------------------------------------


class TestBodySizeLimit:
    """BodySizeLimitMiddleware rejects requests > max_bytes with 413."""

    @pytest.fixture()
    def body_limit_app(self, proxy_settings: ProxySettings):
        """App with body size middleware registered."""
        app = create_app(proxy_settings)
        return app

    @pytest.fixture()
    async def body_limit_client(self, body_limit_app, repo):
        """Client with body size middleware and manually set state."""
        import aiosqlite

        from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule

        db = await aiosqlite.connect(body_limit_app.state.settings.db_path)
        body_limit_app.state.db = db
        body_limit_app.state.repo = repo
        body_limit_app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
        body_limit_app.state.rules_engine = RulesEngine(
            rules=[
                SpendCapRule(db=db),
                RateLimitRule(default_rps=100.0),
            ]
        )
        transport = httpx.ASGITransport(app=body_limit_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await body_limit_app.state.httpx_client.aclose()
        await db.close()

    async def test_oversized_request_returns_413(self, body_limit_client):
        """Request with Content-Length > 10MB returns 413."""
        resp = await body_limit_client.post(
            "/v1/chat/completions",
            headers={
                "content-length": str(11 * 1024 * 1024),
                "x-worthless-alias": "test",
                "x-worthless-shard-a": "dGVzdA==",
            },
            content=b"x",  # actual body doesn't matter, header is checked
        )
        assert resp.status_code == 413
        import json

        body = json.loads(resp.content)
        assert "error" in body

    async def test_normal_request_passes_through(self, body_limit_client):
        """Request with Content-Length <= 10MB passes through to handler."""
        resp = await body_limit_client.post(
            "/v1/chat/completions",
            headers={
                "content-length": "100",
                "x-worthless-alias": "test",
            },
            content=b"x" * 100,
        )
        # Should reach the handler (401 because no valid shard, but NOT 413)
        assert resp.status_code != 413

    async def test_no_content_length_passes_through(self, body_limit_client):
        """Request without Content-Length header passes through (streaming uploads)."""
        resp = await body_limit_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-alias": "test",
            },
            content=b"small body",
        )
        # Should reach the handler, not be rejected by middleware
        assert resp.status_code != 413


# ------------------------------------------------------------------
# M-11: CORS Denial
# ------------------------------------------------------------------


class TestCORSDenial:
    """CORS is explicitly denied — no Access-Control-Allow-Origin in responses."""

    @pytest.fixture()
    async def cors_client(self, proxy_settings: ProxySettings, repo):
        """Client for CORS testing."""
        import aiosqlite

        from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule

        app = create_app(proxy_settings)
        db = await aiosqlite.connect(proxy_settings.db_path)
        app.state.db = db
        app.state.repo = repo
        app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
        app.state.rules_engine = RulesEngine(
            rules=[
                SpendCapRule(db=db),
                RateLimitRule(default_rps=100.0),
            ]
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await app.state.httpx_client.aclose()
        await db.close()

    async def test_cors_preflight_denied(self, cors_client):
        """CORS preflight (OPTIONS with Origin) gets no Access-Control-Allow-Origin."""
        resp = await cors_client.options(
            "/v1/chat/completions",
            headers={
                "origin": "https://evil.com",
                "access-control-request-method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers

    async def test_regular_request_no_cors_header(self, cors_client):
        """Regular request has no Access-Control-Allow-Origin header."""
        resp = await cors_client.post(
            "/healthz",
            headers={"origin": "https://evil.com"},
        )
        assert "access-control-allow-origin" not in resp.headers
