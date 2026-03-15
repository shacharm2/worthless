"""Anthropic provider adapter — stateless request/response transformer."""

from __future__ import annotations

import httpx

from worthless.adapters.types import AdapterRequest, AdapterResponse

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
        out_headers: dict[str, str] = {}
        has_version = False

        for key, value in headers.items():
            lower = key.lower()
            if lower.startswith("x-worthless-"):
                continue
            if lower == "anthropic-version":
                has_version = True
            out_headers[lower] = value

        out_headers["x-api-key"] = api_key

        if not has_version:
            out_headers["anthropic-version"] = DEFAULT_ANTHROPIC_VERSION

        return AdapterRequest(url=UPSTREAM_URL, headers=out_headers, body=body)

    async def relay_response(self, response: httpx.Response) -> AdapterResponse:
        return AdapterResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
            is_streaming=False,
        )
