"""Token extraction from provider responses and async spend recording."""

from __future__ import annotations

import json

import aiosqlite


def extract_usage_openai(data: bytes) -> int:
    """Extract total_tokens from an OpenAI response (JSON or SSE).

    For JSON responses: parses usage.total_tokens directly.
    For SSE streams: scans for the final chunk containing a "usage" field.
    Returns 0 if usage data is not found or data is malformed.
    """
    if not data:
        return 0

    # Try as plain JSON first
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "usage" in parsed:
            return parsed["usage"].get("total_tokens", 0)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try as SSE: scan for data: lines containing "usage"
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
                    return chunk["usage"].get("total_tokens", 0)
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception:
        pass

    return 0


def extract_usage_anthropic(data: bytes) -> int:
    """Extract output_tokens from an Anthropic SSE response.

    Scans for event: message_delta followed by data containing usage.output_tokens.
    Returns 0 if usage data is not found or data is malformed.
    """
    if not data:
        return 0

    try:
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "event: message_delta":
                # Next non-empty line should be the data line
                for j in range(i + 1, len(lines)):
                    data_line = lines[j].strip()
                    if data_line.startswith("data: "):
                        try:
                            chunk = json.loads(data_line[6:])
                            if isinstance(chunk, dict) and "usage" in chunk:
                                return chunk["usage"].get("output_tokens", 0)
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break
    except Exception:
        pass

    return 0


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
