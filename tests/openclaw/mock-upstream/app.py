"""Mock upstream provider — captures Authorization headers for test assertions.

Tiny FastAPI app that pretends to be an OpenAI-compatible API.
Returns valid chat completion JSON while recording every Authorization
header it receives, so tests can verify the real key (not shard-A)
arrived at the upstream.
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


def _chat_completion_body(model: str = "gpt-4o", streaming: bool = False) -> dict:
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


def _stream_chunks(model: str = "gpt-4o"):
    """Yield SSE chunks mimicking OpenAI streaming format."""
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    # Content chunk
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

    # Final chunk
    done_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done_chunk)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Capture headers and return a valid chat completion."""
    auth = request.headers.get("authorization", "")
    with _lock:
        _captured_headers.append(
            {
                "authorization": auth,
                "timestamp": str(time.time()),
            }
        )

    body = await request.json()
    model = body.get("model", "gpt-4o")
    stream = body.get("stream", False)

    if stream:
        return StreamingResponse(
            _stream_chunks(model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return JSONResponse(_chat_completion_body(model))


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
