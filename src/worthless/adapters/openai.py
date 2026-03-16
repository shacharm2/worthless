"""OpenAI provider adapter — stateless request/response transformer."""

from __future__ import annotations

import httpx

from worthless.adapters.types import (
    AdapterRequest,
    AdapterResponse,
    relay_response,
    strip_internal_headers,
)

UPSTREAM_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIAdapter:
    """Transforms requests for the OpenAI chat completions API."""

    def prepare_request(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        api_key: str,
    ) -> AdapterRequest:
        out_headers = strip_internal_headers(headers)
        out_headers["authorization"] = f"Bearer {api_key}"
        return AdapterRequest(url=UPSTREAM_URL, headers=out_headers, body=body)

    async def relay_response(self, response: httpx.Response) -> AdapterResponse:
        return await relay_response(response)
