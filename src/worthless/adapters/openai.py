"""OpenAI provider adapter — stateless request/response transformer."""

from __future__ import annotations

import httpx

from worthless.adapters.types import AdapterRequest, AdapterResponse

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
        out_headers: dict[str, str] = {}
        for key, value in headers.items():
            lower = key.lower()
            if lower.startswith("x-worthless-"):
                continue
            out_headers[lower] = value

        out_headers["authorization"] = f"Bearer {api_key}"

        return AdapterRequest(url=UPSTREAM_URL, headers=out_headers, body=body)

    async def relay_response(self, response: httpx.Response) -> AdapterResponse:
        return AdapterResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
            is_streaming=False,
        )
