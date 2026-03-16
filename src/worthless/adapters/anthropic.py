"""Anthropic provider adapter — stateless request/response transformer."""

from __future__ import annotations

import httpx

from worthless.adapters.types import (
    AdapterRequest,
    AdapterResponse,
    relay_response,
    strip_internal_headers,
)

UPSTREAM_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter:
    """Transforms requests for the Anthropic messages API."""

    def prepare_request(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        api_key: str,
    ) -> AdapterRequest:
        out_headers = strip_internal_headers(headers)
        out_headers["x-api-key"] = api_key

        if "anthropic-version" not in out_headers:
            out_headers["anthropic-version"] = DEFAULT_ANTHROPIC_VERSION

        return AdapterRequest(url=UPSTREAM_URL, headers=out_headers, body=body)

    async def relay_response(self, response: httpx.Response) -> AdapterResponse:
        return await relay_response(response)
