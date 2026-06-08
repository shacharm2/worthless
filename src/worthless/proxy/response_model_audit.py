"""Response-model extraction for the WOR-696 mismatch audit.

Pure parser. Given one SSE chunk, return the first ``model`` value found,
or ``None``. The handler in ``proxy/app.py`` calls this once per stream
(short-circuits after first observation), compares against the request's
``model``, and increments ``app.state.response_model_mismatch_counter``
on mismatch. Observation only — never blocks, never mutates the chunk.

Wire format
-----------
- OpenAI Chat Completions SSE: every chunk includes ``"model":"gpt-..."``
  at top level.
- Anthropic Messages SSE: the ``message_start`` event carries
  ``"message":{"model":"claude-...","..."}``.

Both shapes funnel through one extractor. Returns ``None`` on any parse
failure — absence of a model in a chunk is normal (e.g. ``[DONE]``).
"""

from __future__ import annotations

import json
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


__all__ = ["extract_response_model"]
