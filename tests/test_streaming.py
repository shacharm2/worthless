"""Tests for SSE streaming relay — PROX-03."""

from __future__ import annotations

import httpx
import pytest

from collections.abc import AsyncIterator

from worthless.adapters.anthropic import AnthropicAdapter
from worthless.adapters.openai import OpenAIAdapter
from worthless.adapters.types import AdapterResponse


# ---------------------------------------------------------------------------
# Helper (duplicated from conftest to avoid import issues)
# ---------------------------------------------------------------------------


class _AsyncByteStream(httpx.AsyncByteStream):
    """Mock async byte stream that yields chunks one at a time."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def make_streaming_response(
    chunks: list[bytes],
    status_code: int = 200,
) -> httpx.Response:
    """Create a mock httpx.Response that streams SSE chunks."""
    stream = _AsyncByteStream(chunks)
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": "text/event-stream"},
        stream=stream,
    )


# ---------------------------------------------------------------------------
# OpenAI SSE relay
# ---------------------------------------------------------------------------


class TestOpenAISSERelay:
    @pytest.mark.asyncio
    async def test_openai_sse_relay(
        self, mock_openai_sse_chunks: list[bytes]
    ) -> None:
        """OpenAI streaming response yields chunks with is_streaming=True."""
        mock_resp = make_streaming_response(mock_openai_sse_chunks)
        adapter = OpenAIAdapter()
        resp = await adapter.relay_response(mock_resp)

        assert isinstance(resp, AdapterResponse)
        assert resp.is_streaming is True
        assert resp.stream is not None

        collected: list[bytes] = []
        async for chunk in resp.stream:
            collected.append(chunk)

        assert len(collected) == len(mock_openai_sse_chunks)
        assert collected == mock_openai_sse_chunks


# ---------------------------------------------------------------------------
# Anthropic SSE relay
# ---------------------------------------------------------------------------


class TestAnthropicSSERelay:
    @pytest.mark.asyncio
    async def test_anthropic_sse_relay(
        self, mock_anthropic_sse_chunks: list[bytes]
    ) -> None:
        """Anthropic streaming response yields chunks with is_streaming=True."""
        mock_resp = make_streaming_response(mock_anthropic_sse_chunks)
        adapter = AnthropicAdapter()
        resp = await adapter.relay_response(mock_resp)

        assert isinstance(resp, AdapterResponse)
        assert resp.is_streaming is True
        assert resp.stream is not None

        collected: list[bytes] = []
        async for chunk in resp.stream:
            collected.append(chunk)

        assert len(collected) == len(mock_anthropic_sse_chunks)
        assert collected == mock_anthropic_sse_chunks


# ---------------------------------------------------------------------------
# SSE headers
# ---------------------------------------------------------------------------


class TestStreamingHeaders:
    @pytest.mark.asyncio
    async def test_streaming_headers(
        self, mock_openai_sse_chunks: list[bytes]
    ) -> None:
        """Streaming response includes correct SSE headers."""
        mock_resp = make_streaming_response(mock_openai_sse_chunks)
        adapter = OpenAIAdapter()
        resp = await adapter.relay_response(mock_resp)

        assert resp.headers["Content-Type"] == "text/event-stream; charset=utf-8"
        assert resp.headers["Cache-Control"] == "no-cache"
        assert resp.headers["X-Accel-Buffering"] == "no"

    @pytest.mark.asyncio
    async def test_streaming_headers_anthropic(
        self, mock_anthropic_sse_chunks: list[bytes]
    ) -> None:
        """Anthropic streaming response also includes correct SSE headers."""
        mock_resp = make_streaming_response(mock_anthropic_sse_chunks)
        adapter = AnthropicAdapter()
        resp = await adapter.relay_response(mock_resp)

        assert resp.headers["Content-Type"] == "text/event-stream; charset=utf-8"
        assert resp.headers["Cache-Control"] == "no-cache"
        assert resp.headers["X-Accel-Buffering"] == "no"


# ---------------------------------------------------------------------------
# No buffering
# ---------------------------------------------------------------------------


class TestNoBuffering:
    @pytest.mark.asyncio
    async def test_no_buffering(
        self, mock_openai_sse_chunks: list[bytes]
    ) -> None:
        """Chunks are yielded individually, not accumulated into one blob."""
        mock_resp = make_streaming_response(mock_openai_sse_chunks)
        adapter = OpenAIAdapter()
        resp = await adapter.relay_response(mock_resp)

        collected: list[bytes] = []
        async for chunk in resp.stream:
            collected.append(chunk)

        # Must have more than 1 chunk (not buffered into single blob)
        assert len(collected) > 1


# ---------------------------------------------------------------------------
# Non-streaming unchanged (regression guard)
# ---------------------------------------------------------------------------


class TestNonStreamingUnchanged:
    @pytest.mark.asyncio
    async def test_non_streaming_unchanged(self) -> None:
        """JSON responses still return non-streaming AdapterResponse."""
        body = b'{"id":"chatcmpl-1","choices":[]}'
        upstream = httpx.Response(
            status_code=200,
            content=body,
            headers={"content-type": "application/json"},
        )
        adapter = OpenAIAdapter()
        resp = await adapter.relay_response(upstream)

        assert resp.is_streaming is False
        assert resp.stream is None
        assert resp.body == body


# ---------------------------------------------------------------------------
# Error passthrough with streaming content-type
# ---------------------------------------------------------------------------


class TestStreamingErrorPassthrough:
    @pytest.mark.asyncio
    async def test_streaming_error_passthrough(
        self, mock_openai_sse_chunks: list[bytes]
    ) -> None:
        """Non-2xx status with streaming content-type is still streamed."""
        mock_resp = make_streaming_response(
            mock_openai_sse_chunks, status_code=429
        )
        adapter = OpenAIAdapter()
        resp = await adapter.relay_response(mock_resp)

        assert resp.status_code == 429
        assert resp.is_streaming is True
        assert resp.stream is not None
