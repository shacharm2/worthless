"""OpenAI provider adapter — stateless request/response transformer."""

from __future__ import annotations

import os

import httpx

from worthless.adapters.types import (
    AdapterRequest,
    AdapterResponse,
    relay_response,
    strip_internal_headers,
)

UPSTREAM_URL = os.environ.get(
    "WORTHLESS_UPSTREAM_OPENAI_URL",
    "https://api.openai.com/v1/chat/completions",
)


class OpenAIAdapter:
    """Transforms requests for the OpenAI chat completions API."""

    def prepare_request(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        api_key: bytearray,
    ) -> AdapterRequest:
        out_headers = strip_internal_headers(headers)
        out_headers["authorization"] = f"Bearer {api_key.decode()}"
        return AdapterRequest(url=UPSTREAM_URL, headers=out_headers, body=body)

    async def relay_response(self, response: httpx.Response) -> AdapterResponse:
        return await relay_response(response)
