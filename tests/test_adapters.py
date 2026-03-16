"""Tests for provider adapters — PROX-01 and PROX-02."""

from __future__ import annotations

import httpx
import pytest

from worthless.adapters.openai import OpenAIAdapter
from worthless.adapters.anthropic import AnthropicAdapter
from worthless.adapters.registry import get_adapter
from worthless.adapters.types import AdapterRequest, AdapterResponse


# ---------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------


class TestOpenAIRequestTransform:
    def test_openai_request_transform(
        self, sample_openai_body: bytes, sample_api_key: str
    ) -> None:
        adapter = OpenAIAdapter()
        req = adapter.prepare_request(
            body=sample_openai_body,
            headers={"content-type": "application/json"},
            api_key=sample_api_key,
        )
        assert isinstance(req, AdapterRequest)
        assert req.url == "https://api.openai.com/v1/chat/completions"
        assert req.headers["authorization"] == f"Bearer {sample_api_key}"
        assert req.body == sample_openai_body


class TestOpenAIResponseRelay:
    @pytest.mark.asyncio
    async def test_openai_response_relay(self) -> None:
        body = b'{"id":"chatcmpl-1","choices":[]}'
        upstream = httpx.Response(
            status_code=200,
            content=body,
            headers={"content-type": "application/json"},
        )
        adapter = OpenAIAdapter()
        resp = await adapter.relay_response(upstream)
        assert isinstance(resp, AdapterResponse)
        assert resp.status_code == 200
        assert resp.body == body
        assert resp.is_streaming is False

    @pytest.mark.asyncio
    async def test_openai_error_relay(self) -> None:
        error_body = b'{"error":{"message":"rate limited","type":"rate_limit"}}'
        upstream = httpx.Response(
            status_code=429,
            content=error_body,
            headers={"content-type": "application/json"},
        )
        adapter = OpenAIAdapter()
        resp = await adapter.relay_response(upstream)
        assert resp.status_code == 429
        assert resp.body == error_body


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------


class TestAnthropicRequestTransform:
    def test_anthropic_request_transform(
        self, sample_anthropic_body: bytes, sample_api_key: str
    ) -> None:
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=sample_anthropic_body,
            headers={"content-type": "application/json"},
            api_key=sample_api_key,
        )
        assert isinstance(req, AdapterRequest)
        assert req.url == "https://api.anthropic.com/v1/messages"
        assert req.headers["x-api-key"] == sample_api_key
        assert req.body == sample_anthropic_body

    def test_anthropic_version_header_default(
        self, sample_anthropic_body: bytes, sample_api_key: str
    ) -> None:
        """Default anthropic-version header is added when client omits it."""
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=sample_anthropic_body,
            headers={"content-type": "application/json"},
            api_key=sample_api_key,
        )
        assert req.headers["anthropic-version"] == "2023-06-01"

    def test_anthropic_version_header_preserved(
        self, sample_anthropic_body: bytes, sample_api_key: str
    ) -> None:
        """Client-provided anthropic-version header is preserved."""
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=sample_anthropic_body,
            headers={
                "content-type": "application/json",
                "anthropic-version": "2024-01-01",
            },
            api_key=sample_api_key,
        )
        assert req.headers["anthropic-version"] == "2024-01-01"


class TestAnthropicResponseRelay:
    @pytest.mark.asyncio
    async def test_anthropic_response_relay(self) -> None:
        body = b'{"id":"msg_01","content":[{"type":"text","text":"Hello"}]}'
        upstream = httpx.Response(
            status_code=200,
            content=body,
            headers={"content-type": "application/json"},
        )
        adapter = AnthropicAdapter()
        resp = await adapter.relay_response(upstream)
        assert isinstance(resp, AdapterResponse)
        assert resp.status_code == 200
        assert resp.body == body
        assert resp.is_streaming is False

    @pytest.mark.asyncio
    async def test_anthropic_error_relay(self) -> None:
        error_body = b'{"type":"error","error":{"type":"invalid_request_error"}}'
        upstream = httpx.Response(
            status_code=400,
            content=error_body,
            headers={"content-type": "application/json"},
        )
        adapter = AnthropicAdapter()
        resp = await adapter.relay_response(upstream)
        assert resp.status_code == 400
        assert resp.body == error_body


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_get_adapter_openai(self) -> None:
        adapter = get_adapter("/v1/chat/completions")
        assert isinstance(adapter, OpenAIAdapter)

    def test_get_adapter_anthropic(self) -> None:
        adapter = get_adapter("/v1/messages")
        assert isinstance(adapter, AnthropicAdapter)

    def test_get_adapter_unknown(self) -> None:
        adapter = get_adapter("/v1/unknown")
        assert adapter is None

    def test_get_adapter_empty_path(self) -> None:
        adapter = get_adapter("")
        assert adapter is None

    def test_get_adapter_returns_singletons(self) -> None:
        """Registry returns the same adapter instance on repeated calls."""
        a1 = get_adapter("/v1/chat/completions")
        a2 = get_adapter("/v1/chat/completions")
        assert a1 is a2

    def test_get_adapter_partial_path_no_match(self) -> None:
        assert get_adapter("/v1/chat") is None
        assert get_adapter("/v1/messages/extra") is None


# ---------------------------------------------------------------------------
# Header stripping (hop-by-hop at adapter level)
# ---------------------------------------------------------------------------


class TestHopByHopStrippingAdapterLevel:
    def test_openai_strips_hop_by_hop(
        self, sample_openai_body: bytes, sample_api_key: str
    ) -> None:
        adapter = OpenAIAdapter()
        req = adapter.prepare_request(
            body=sample_openai_body,
            headers={
                "content-type": "application/json",
                "host": "attacker.example.com",
                "transfer-encoding": "chunked",
                "connection": "close",
            },
            api_key=sample_api_key,
        )
        for key in req.headers:
            assert key not in ("host", "transfer-encoding", "connection")

    def test_anthropic_strips_hop_by_hop(
        self, sample_anthropic_body: bytes, sample_api_key: str
    ) -> None:
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=sample_anthropic_body,
            headers={
                "content-type": "application/json",
                "host": "attacker.example.com",
                "proxy-authorization": "Basic evil",
            },
            api_key=sample_api_key,
        )
        for key in req.headers:
            assert key not in ("host", "proxy-authorization")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_openai_empty_body(self, sample_api_key: str) -> None:
        adapter = OpenAIAdapter()
        req = adapter.prepare_request(
            body=b"", headers={}, api_key=sample_api_key
        )
        assert req.body == b""
        assert req.url == "https://api.openai.com/v1/chat/completions"

    def test_anthropic_empty_body(self, sample_api_key: str) -> None:
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=b"", headers={}, api_key=sample_api_key
        )
        assert req.body == b""
        assert req.url == "https://api.anthropic.com/v1/messages"

    def test_openai_unicode_body(self, sample_api_key: str) -> None:
        body = '{"messages":[{"role":"user","content":"Hello \U0001f600"}]}'.encode()
        adapter = OpenAIAdapter()
        req = adapter.prepare_request(
            body=body, headers={}, api_key=sample_api_key
        )
        assert req.body == body

    def test_anthropic_preserves_extra_headers(self, sample_api_key: str) -> None:
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=b"{}",
            headers={"content-type": "application/json", "x-custom": "val"},
            api_key=sample_api_key,
        )
        assert req.headers["x-custom"] == "val"

    def test_openai_preserves_extra_headers(self, sample_api_key: str) -> None:
        adapter = OpenAIAdapter()
        req = adapter.prepare_request(
            body=b"{}",
            headers={"content-type": "application/json", "x-custom": "val"},
            api_key=sample_api_key,
        )
        assert req.headers["x-custom"] == "val"

    def test_openai_api_key_overrides_existing_auth(self, sample_api_key: str) -> None:
        """If incoming headers contain authorization, it gets replaced."""
        adapter = OpenAIAdapter()
        req = adapter.prepare_request(
            body=b"{}",
            headers={"authorization": "Bearer old-key"},
            api_key=sample_api_key,
        )
        assert req.headers["authorization"] == f"Bearer {sample_api_key}"

    def test_anthropic_api_key_overrides_existing(self, sample_api_key: str) -> None:
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=b"{}",
            headers={"x-api-key": "old-key"},
            api_key=sample_api_key,
        )
        assert req.headers["x-api-key"] == sample_api_key


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------


class TestHeaderStripping:
    def test_openai_header_stripping(
        self, sample_openai_body: bytes, sample_api_key: str
    ) -> None:
        """Headers matching x-worthless-* are stripped before forwarding."""
        adapter = OpenAIAdapter()
        req = adapter.prepare_request(
            body=sample_openai_body,
            headers={
                "content-type": "application/json",
                "x-worthless-trace-id": "abc123",
                "x-worthless-session": "xyz",
            },
            api_key=sample_api_key,
        )
        for key in req.headers:
            assert not key.lower().startswith("x-worthless-")

    def test_anthropic_header_stripping(
        self, sample_anthropic_body: bytes, sample_api_key: str
    ) -> None:
        """Anthropic adapter also strips x-worthless-* headers."""
        adapter = AnthropicAdapter()
        req = adapter.prepare_request(
            body=sample_anthropic_body,
            headers={
                "content-type": "application/json",
                "x-worthless-trace-id": "abc123",
                "x-worthless-session": "xyz",
            },
            api_key=sample_api_key,
        )
        for key in req.headers:
            assert not key.lower().startswith("x-worthless-")
