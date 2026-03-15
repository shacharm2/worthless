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


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------


class TestHeaderStripping:
    def test_header_stripping(
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
