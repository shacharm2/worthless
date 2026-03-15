"""Adapter contracts for provider request/response transformation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

import httpx


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
