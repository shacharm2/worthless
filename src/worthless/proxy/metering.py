"""Token extraction from provider responses and async spend recording."""

from __future__ import annotations

import json
from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True)
class UsageInfo:
    """Extracted token usage from a provider response."""

    total_tokens: int
    model: str | None


def extract_usage_openai(data: bytes) -> UsageInfo | None:
    """Extract token usage from an OpenAI response (JSON or SSE).

    For JSON responses: parses usage.total_tokens and model directly.
    For SSE streams: scans for the final chunk containing a "usage" field.
    Returns None if usage data is not found or data is malformed.
    """
    if not data:
        return None

    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "usage" in parsed:
            total = parsed["usage"].get("total_tokens", 0)
            return UsageInfo(total_tokens=total, model=parsed.get("model"))
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        text = data.decode("utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
                if isinstance(chunk, dict) and "usage" in chunk:
                    total = chunk["usage"].get("total_tokens", 0)
                    return UsageInfo(total_tokens=total, model=chunk.get("model"))
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception:  # noqa: S110 — best-effort SSE decode; malformed response must not raise
        pass

    return None


def _find_sse_event_data(
    lines: list[str],
    event_name: str,
    *,
    reverse: bool = False,
) -> dict | None:
    """Find an SSE event by name and parse its data payload."""
    indices = range(len(lines) - 1, -1, -1) if reverse else range(len(lines))
    for i in indices:
        if lines[i].strip() == f"event: {event_name}":
            for j in range(i + 1, len(lines)):
                data_line = lines[j].strip()
                if data_line.startswith("data: "):
                    try:
                        return json.loads(data_line[6:])
                    except (json.JSONDecodeError, ValueError):
                        return None
    return None


def extract_usage_anthropic(data: bytes) -> UsageInfo | None:
    """Extract token usage from an Anthropic response (JSON or SSE).

    For non-streaming JSON: parses usage.input_tokens + usage.output_tokens directly.
    For SSE streams: scans for message_start (input_tokens) and message_delta (output_tokens).
    Returns None if no usage data found.
    """
    if not data:
        return None

    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "usage" in parsed:
            usage = parsed["usage"]
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            return UsageInfo(
                total_tokens=input_tokens + output_tokens,
                model=parsed.get("model"),
            )
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
    except Exception:  # noqa: S110 — best-effort SSE decode; malformed response must not raise
        return None

    input_tokens = 0
    model: str | None = None

    start = _find_sse_event_data(lines, "message_start")
    if start:
        msg = start.get("message", {})
        input_tokens = msg.get("usage", {}).get("input_tokens", 0)
        model = msg.get("model")

    delta = _find_sse_event_data(lines, "message_delta", reverse=True)
    if delta is None or "usage" not in delta:
        return None

    output_tokens = delta["usage"].get("output_tokens", 0)
    return UsageInfo(total_tokens=input_tokens + output_tokens, model=model)


async def record_spend(
    db_path: str,
    alias: str,
    tokens: int,
    model: str | None,
    provider: str,
) -> None:
    """Insert a spend record into the spend_log table."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            (alias, tokens, model, provider),
        )
        await db.commit()
