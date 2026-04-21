"""Mock upstream provider — captures auth headers for test assertions.

Tiny FastAPI app that pretends to be an OpenAI- or Anthropic-compatible
API. Returns valid completion JSON while recording every auth header
it receives, so tests can verify the real key (not shard-A) arrived
at the upstream.

Bad-model convention: any model name containing "does-not-exist" makes
OpenAI return 404 and Anthropic return 400 with the respective
provider's error body shape.
"""

from __future__ import annotations

import json
import time
import uuid
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

_captured_headers: list[dict[str, str]] = []
_lock = Lock()


def _is_bad_model(model: str) -> bool:
    return "does-not-exist" in model


def _is_trigger_5xx(model: str) -> bool:
    """Test convention: a model name containing 'trigger-5xx' makes the mock
    simulate an upstream 500 Internal Server Error. Used by Compose-lane
    tests to prove typed SDK exceptions on 5xx passthrough."""
    return "trigger-5xx" in model


def _chat_completion_body(model: str = "gpt-4o") -> dict:
    """Build a valid OpenAI chat completion response body."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello from mock upstream!",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }


def _openai_not_found_body(model: str) -> dict:
    return {
        "error": {
            "message": f"The model `{model}` does not exist or you do not have access to it.",
            "type": "invalid_request_error",
            "param": None,
            "code": "model_not_found",
        }
    }


def _messages_body(model: str) -> dict:
    """Build a valid Anthropic Messages response body."""
    return {
        "id": f"msg_mock_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hello from mock upstream!"}],
        "model": model,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 8},
    }


def _anthropic_bad_model_body(model: str) -> dict:
    return {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": f"model: {model} is not a recognized model name.",
        },
    }


def _stream_chunks(model: str = "gpt-4o", include_usage: bool = False):
    """Yield SSE chunks mimicking OpenAI streaming format.

    When include_usage=True (set by client via stream_options), emit an
    additional terminal chunk with populated usage — matching real OpenAI
    behavior. Without the flag, no usage data appears in the stream, and
    any downstream metering has nothing to extract.
    """
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "Hello!"},
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(chunk)}\n\n"

    done_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done_chunk)}\n\n"

    if include_usage:
        usage_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        yield f"data: {json.dumps(usage_chunk)}\n\n"

    yield "data: [DONE]\n\n"


def _anthropic_stream_events(model: str = "claude-haiku-4-5-20251001"):
    """Yield SSE events for Anthropic Messages streaming.

    The Anthropic SDK's .messages.stream() iterator requires an exact event
    sequence and exact payload shapes. In particular, delta.type MUST be
    "text_delta" on content_block_delta events — otherwise stream.text_stream
    silently yields zero with no error.
    """
    message_id = f"msg_mock_{uuid.uuid4().hex[:12]}"

    start = {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        },
    }
    yield f"event: message_start\ndata: {json.dumps(start)}\n\n"

    block_start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    yield f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n"

    delta = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Hello!"},
    }
    yield f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n"

    block_stop = {"type": "content_block_stop", "index": 0}
    yield f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n"

    message_delta = {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 1},
    }
    yield f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"

    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Capture Authorization header and return an OpenAI chat completion."""
    auth = request.headers.get("authorization", "")
    with _lock:
        _captured_headers.append(
            {
                "header_key": "authorization",
                "authorization": auth,
                "x-api-key": "",
                "provider": "openai",
                "timestamp": str(time.time()),
            }
        )

    body = await request.json()
    model = body.get("model", "gpt-4o")

    if _is_trigger_5xx(model):
        return JSONResponse(
            {
                "error": {
                    "message": "upstream overloaded",
                    "type": "server_error",
                    "param": None,
                    "code": None,
                }
            },
            status_code=500,
        )
    if _is_bad_model(model):
        return JSONResponse(_openai_not_found_body(model), status_code=404)

    stream = body.get("stream", False)
    if stream:
        include_usage = bool(body.get("stream_options", {}).get("include_usage", False))
        return StreamingResponse(
            _stream_chunks(model, include_usage=include_usage),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return JSONResponse(_chat_completion_body(model))


@app.post("/v1/messages")
async def messages(request: Request):
    """Capture x-api-key header and return an Anthropic Messages response."""
    api_key = request.headers.get("x-api-key", "")
    with _lock:
        _captured_headers.append(
            {
                "header_key": "x-api-key",
                "authorization": "",
                "x-api-key": api_key,
                "provider": "anthropic",
                "timestamp": str(time.time()),
            }
        )

    body = await request.json()
    model = body.get("model", "claude-haiku-4-5-20251001")

    if _is_trigger_5xx(model):
        return JSONResponse(
            {
                "type": "error",
                "error": {"type": "api_error", "message": "upstream overloaded"},
            },
            status_code=500,
        )
    if _is_bad_model(model):
        return JSONResponse(_anthropic_bad_model_body(model), status_code=400)

    stream = body.get("stream", False)
    if stream:
        return StreamingResponse(
            _anthropic_stream_events(model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return JSONResponse(_messages_body(model))


@app.get("/captured-headers")
async def get_captured_headers():
    """Return all captured Authorization headers for test assertions."""
    with _lock:
        return JSONResponse({"headers": list(_captured_headers)})


@app.delete("/captured-headers")
async def clear_captured_headers():
    """Reset captured headers between test runs."""
    with _lock:
        _captured_headers.clear()
    return JSONResponse({"status": "cleared"})


@app.get("/healthz")
async def healthz():
    return JSONResponse({"status": "ok"})
