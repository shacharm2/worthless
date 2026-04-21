"""OpenAI provider adapter — stateless request/response transformer."""

from __future__ import annotations

import json
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


def _inject_include_usage(body: bytes) -> bytes:
    """Ensure streaming requests always receive a final usage chunk.

    OpenAI only emits token counts in the SSE stream when
    ``stream_options.include_usage`` is ``true``.  Without it the proxy
    meters 0 tokens and spend caps silently never fire (WOR-240).

    Returns the original ``body`` bytes unchanged when no injection is
    required so callers can detect whether the body was modified (byte
    identity comparison) and skip updating ``Content-Length`` accordingly.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body

    if not payload.get("stream"):
        return body

    # Already set — return original bytes; no re-serialisation needed.
    if payload.get("stream_options", {}).get("include_usage"):
        return body

    payload.setdefault("stream_options", {})["include_usage"] = True
    return json.dumps(payload).encode()


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
        new_body = _inject_include_usage(body)
        if new_body is not body:
            # Body was modified: stale Content-Length must be removed so
            # httpx recalculates it from the actual byte count (WOR-240).
            out_headers.pop("content-length", None)
        return AdapterRequest(url=UPSTREAM_URL, headers=out_headers, body=new_body)

    async def relay_response(self, response: httpx.Response) -> AdapterResponse:
        return await relay_response(response)
