"""Response-model mismatch audit (WOR-696).

Observation-only. When the upstream provider responds with a `model` value
that differs from what the request asked for (silent re-route from
gpt-4o-mini → gpt-5, or claude-haiku → claude-opus, etc.), this module
records a counter increment on ``app.state.response_model_mismatch_counter``
keyed by ``(request_model, response_model)``.

Passthrough invariant: this module NEVER mutates the chunk, NEVER blocks
the stream, and NEVER raises into the streaming path. Audit failure must
not turn a successful upstream call into a user-visible error.

Wire format
-----------
- OpenAI Chat Completions SSE: every chunk includes ``"model":"gpt-..."``
  at top level.
- Anthropic Messages SSE: the ``message_start`` event carries
  ``"message":{"model":"claude-...","..."}``.

Both shapes funnel through one extractor that scans ``data: {...}`` lines
in the chunk and returns the first ``model`` field found at the top level
or one level deep under ``message``. Returns ``None`` on any parse failure
— absence of a model in a chunk is normal (e.g. ``[DONE]``).
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from typing import Any


def extract_response_model(chunk: bytes) -> str | None:
    """Return the response ``model`` value from one SSE chunk, or ``None``.

    Scans every ``data: {...}`` line in the chunk and returns the first
    ``model`` string found at the top level or nested under ``message``.
    Tolerates partial / multi-event chunks. Never raises — bad JSON,
    binary noise, or an empty chunk all yield ``None``.
    """
    if not chunk:
        return None
    try:
        text = chunk.decode("utf-8", errors="ignore")
    except Exception:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj: Any = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        model = obj.get("model")
        if isinstance(model, str) and model:
            return model
        message = obj.get("message")
        if isinstance(message, dict):
            nested = message.get("model")
            if isinstance(nested, str) and nested:
                return nested
    return None


def record_if_mismatch(
    counter: MutableMapping[tuple[str, str], int],
    request_model: str | None,
    chunk: bytes,
) -> None:
    """Increment ``counter[(request_model, response_model)]`` on mismatch.

    No-op if either side is missing or empty, or if request_model and
    response_model are identical. Never raises — audit must not interfere
    with the stream.
    """
    if not request_model:
        return
    try:
        response_model = extract_response_model(chunk)
    except Exception:
        return
    if not response_model:
        return
    if response_model == request_model:
        return
    key = (request_model, response_model)
    try:
        counter[key] = counter.get(key, 0) + 1
    except Exception:
        # Counter is supposed to be a dict-like — defensive no-op if not.
        return


__all__ = ["extract_response_model", "record_if_mismatch"]
