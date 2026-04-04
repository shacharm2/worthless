#!/usr/bin/env python3
"""TestSprite harness — mock upstream + enrollment + real proxy.

Starts a mock OpenAI/Anthropic server on port 9000 and the real Worthless
proxy on port 8000 with fake enrolled keys.  TestSprite (or curl) can hit
localhost:8000 and get realistic responses through the full auth pipeline.

Zero product code changes.  This is a test script, not shipped code.

Usage:
    uv run python scripts/testsprite_harness.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

# Allow importing from tests/ (not an installed package)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from cryptography.fernet import Fernet
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

import worthless.adapters.anthropic as _anth_mod
import worthless.adapters.openai as _oai_mod
from worthless.cli.enroll_stub import enroll_stub
from worthless.proxy.app import create_app
from worthless.proxy.config import ProxySettings

from tests.helpers import fake_anthropic_key, fake_openai_key

log = logging.getLogger("testsprite-harness")

MOCK_PORT = 9000
PROXY_PORT = 8000
MOCK_UPSTREAM = f"http://127.0.0.1:{MOCK_PORT}"

# ---------------------------------------------------------------------------
# Mock upstream server
# ---------------------------------------------------------------------------

MOCK_OPENAI_RESPONSE = {
    "id": "chatcmpl-test-worthless-mock",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello from Worthless mock upstream!",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
}

MOCK_ANTHROPIC_RESPONSE = {
    "id": "msg-test-worthless-mock",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Hello from Worthless mock upstream!"}],
    "model": "claude-3-5-sonnet-20241022",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 8},
}


async def mock_openai(request: Request) -> JSONResponse:
    body = await request.body()
    parsed = json.loads(body) if body else {}
    if parsed.get("stream"):
        return StreamingResponse(
            _openai_sse_stream(),
            media_type="text/event-stream",
            headers={"x-request-id": "req-test-mock"},
        )
    return JSONResponse(MOCK_OPENAI_RESPONSE, headers={"x-request-id": "req-test-mock"})


async def mock_anthropic(request: Request) -> JSONResponse:
    body = await request.body()
    parsed = json.loads(body) if body else {}
    if parsed.get("stream"):
        return StreamingResponse(
            _anthropic_sse_stream(),
            media_type="text/event-stream",
            headers={"request-id": "req-test-mock"},
        )
    return JSONResponse(MOCK_ANTHROPIC_RESPONSE, headers={"request-id": "req-test-mock"})


async def _openai_sse_stream():
    chunk = {
        "id": "chatcmpl-test-stream",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "gpt-4",
        "choices": [{"index": 0, "delta": {"content": "Hello!"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(chunk)}\n\n"
    done_chunk = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    done_chunk["usage"] = {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
    yield f"data: {json.dumps(done_chunk)}\n\n"
    yield "data: [DONE]\n\n"


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _anthropic_sse_stream():
    yield _sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg-test-stream",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-3-5-sonnet-20241022",
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        },
    )
    yield _sse(
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    )
    yield _sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello!"},
        },
    )
    yield _sse(
        "content_block_stop",
        {
            "type": "content_block_stop",
            "index": 0,
        },
    )
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1},
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


MOCK_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "gpt-4", "object": "model", "owned_by": "openai"},
        {"id": "gpt-3.5-turbo", "object": "model", "owned_by": "openai"},
    ],
}


async def mock_models(request: Request) -> JSONResponse:
    """TestSprite probes /v1/models for discovery."""
    return JSONResponse(MOCK_MODELS_RESPONSE)


async def mock_catchall(request: Request) -> JSONResponse:
    """Return provider-shaped 404 for unknown paths (not Starlette's HTML 404)."""
    return JSONResponse(
        {
            "error": {
                "message": f"Unknown endpoint: {request.url.path}",
                "type": "invalid_request_error",
                "param": None,
                "code": None,
            }
        },
        status_code=404,
    )


mock_app = Starlette(
    routes=[
        Route("/v1/chat/completions", mock_openai, methods=["POST"]),
        Route("/v1/messages", mock_anthropic, methods=["POST"]),
        Route("/v1/models", mock_models, methods=["GET"]),
        Route("/{path:path}", mock_catchall),
    ]
)

# ---------------------------------------------------------------------------
# Enrollment + proxy startup
# ---------------------------------------------------------------------------


def _fake_keys() -> dict[str, str]:
    return {"openai": fake_openai_key(), "anthropic": fake_anthropic_key()}


async def enroll_fake_keys(db_path: str, fernet_key: bytes, shard_a_dir: str) -> None:
    for provider, api_key in _fake_keys().items():
        alias = f"{provider}-testsprite"
        await enroll_stub(
            alias=alias,
            api_key=api_key,
            provider=provider,
            db_path=db_path,
            fernet_key=fernet_key,
            shard_a_dir=shard_a_dir,
        )
        log.info("Enrolled %s (%s)", alias, provider)


def patch_upstream_urls() -> None:
    _oai_mod.UPSTREAM_URL = f"{MOCK_UPSTREAM}/v1/chat/completions"
    _anth_mod.UPSTREAM_URL = f"{MOCK_UPSTREAM}/v1/messages"

    # Guard: fail loud if a refactor makes adapters capture URL at init time
    assert MOCK_UPSTREAM in _oai_mod.UPSTREAM_URL, (
        "OpenAI adapter UPSTREAM_URL not patched — adapters may cache URL at import"
    )
    assert MOCK_UPSTREAM in _anth_mod.UPSTREAM_URL, "Anthropic adapter UPSTREAM_URL not patched"
    log.info("Patched UPSTREAM_URL -> %s", MOCK_UPSTREAM)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="worthless-testsprite-") as tmpdir:
        db_path = str(Path(tmpdir) / "worthless.db")
        shard_a_dir = str(Path(tmpdir) / "shard_a")
        fernet_key = Fernet.generate_key()

        log.info("TestSprite harness — tmp=%s", tmpdir)

        await enroll_fake_keys(db_path, fernet_key, shard_a_dir)
        patch_upstream_urls()
        settings = ProxySettings(
            db_path=db_path,
            fernet_key=fernet_key.decode(),
            shard_a_dir=shard_a_dir,
            allow_insecure=True,
            allow_alias_inference=True,
            default_rate_limit_rps=100.0,
        )
        proxy_app = create_app(settings)

        log.info("Starting mock upstream on :%d", MOCK_PORT)
        log.info("Starting real proxy on :%d", PROXY_PORT)

        mock_server = uvicorn.Server(
            uvicorn.Config(mock_app, host="127.0.0.1", port=MOCK_PORT, log_level="warning")
        )
        proxy_server = uvicorn.Server(
            uvicorn.Config(proxy_app, host="127.0.0.1", port=PROXY_PORT, log_level="info")
        )

        await asyncio.gather(
            mock_server.serve(),
            proxy_server.serve(),
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down.")
