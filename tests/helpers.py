"""Shared test helpers for the worthless test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx


class MockAsyncByteStream(httpx.AsyncByteStream):
    """Mock async byte stream that yields chunks one at a time."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def make_streaming_response(
    chunks: list[bytes],
    status_code: int = 200,
    headers: dict[str, Any] | None = None,
) -> httpx.Response:
    """Create a mock httpx.Response that streams SSE chunks."""
    _headers = {"content-type": "text/event-stream"}
    if headers:
        _headers.update(headers)
    stream = MockAsyncByteStream(chunks)
    return httpx.Response(
        status_code=status_code,
        headers=_headers,
        stream=stream,
    )
