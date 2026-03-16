"""Adapter contracts for provider request/response transformation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

import httpx

INTERNAL_HEADER_PREFIX = "x-worthless-"

SSE_RESPONSE_HEADERS: dict[str, str] = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@dataclass(frozen=True)
class AdapterRequest:
    """Upstream request prepared by a provider adapter."""

    url: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class AdapterResponse:
    """Upstream response relayed by a provider adapter."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    is_streaming: bool = False
    stream: AsyncIterator[bytes] | None = field(default=None, compare=False)


class ProviderAdapter(Protocol):
    """Protocol that all provider adapters must satisfy."""

    def prepare_request(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        api_key: str,
    ) -> AdapterRequest: ...

    async def relay_response(self, response: httpx.Response) -> AdapterResponse: ...


def strip_internal_headers(headers: dict[str, str]) -> dict[str, str]:
    """Copy headers, dropping x-worthless-* and lowercasing keys."""
    return {
        low: v
        for k, v in headers.items()
        if not (low := k.lower()).startswith(INTERNAL_HEADER_PREFIX)
    }


async def relay_response(response: httpx.Response) -> AdapterResponse:
    """Shared relay logic for all adapters — handles streaming and non-streaming."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        return AdapterResponse(
            status_code=response.status_code,
            headers=dict(SSE_RESPONSE_HEADERS),
            body=b"",
            is_streaming=True,
            stream=response.aiter_bytes(),
        )
    return AdapterResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        body=response.content,
        is_streaming=False,
    )
