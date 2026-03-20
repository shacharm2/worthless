"""Tests for metering — token extraction from OpenAI and Anthropic responses."""

from __future__ import annotations

import json

import pytest

from worthless.proxy.metering import (
    extract_usage_anthropic,
    extract_usage_openai,
    record_spend,
)


# ---------------------------------------------------------------------------
# OpenAI token extraction
# ---------------------------------------------------------------------------


def test_extract_usage_openai_json():
    """Standard JSON response with usage.total_tokens."""
    data = json.dumps(
        {
            "id": "chatcmpl-abc",
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
    ).encode()
    assert extract_usage_openai(data) == 30


def test_extract_usage_openai_sse():
    """SSE stream with final chunk containing usage field."""
    chunks = (
        b"data: " + json.dumps({"choices": [{"delta": {"content": "Hi"}}]}).encode() + b"\n\n"
        b"data: "
        + json.dumps(
            {
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 15, "total_tokens": 20},
            }
        ).encode()
        + b"\n\n"
        b"data: [DONE]\n\n"
    )
    assert extract_usage_openai(chunks) == 20


def test_extract_usage_openai_missing():
    """No usage field -> return 0."""
    data = json.dumps({"id": "chatcmpl-abc", "choices": []}).encode()
    assert extract_usage_openai(data) == 0


def test_extract_usage_openai_empty():
    """Empty bytes -> return 0."""
    assert extract_usage_openai(b"") == 0


def test_extract_usage_openai_malformed():
    """Malformed JSON -> return 0 (no crash)."""
    assert extract_usage_openai(b"{not valid json") == 0


# ---------------------------------------------------------------------------
# Anthropic token extraction
# ---------------------------------------------------------------------------


def test_extract_usage_anthropic_message_delta():
    """SSE with message_delta event containing usage.output_tokens."""
    sse_data = (
        b"event: content_block_delta\n"
        b"data: {\"type\": \"content_block_delta\", \"delta\": {\"text\": \"Hi\"}}\n\n"
        b"event: message_delta\n"
        b"data: "
        + json.dumps(
            {
                "type": "message_delta",
                "usage": {"output_tokens": 42},
            }
        ).encode()
        + b"\n\n"
    )
    assert extract_usage_anthropic(sse_data) == 42


def test_extract_usage_anthropic_missing():
    """No message_delta event -> return 0."""
    sse_data = (
        b"event: content_block_delta\n"
        b"data: {\"type\": \"content_block_delta\", \"delta\": {\"text\": \"Hi\"}}\n\n"
    )
    assert extract_usage_anthropic(sse_data) == 0


def test_extract_usage_anthropic_empty():
    """Empty bytes -> return 0."""
    assert extract_usage_anthropic(b"") == 0


def test_extract_usage_anthropic_malformed():
    """Malformed data -> return 0 (no crash)."""
    assert extract_usage_anthropic(b"event: message_delta\ndata: broken{json\n\n") == 0


# ---------------------------------------------------------------------------
# record_spend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_spend(tmp_path):
    """record_spend inserts a row into spend_log."""
    import aiosqlite

    from worthless.storage.schema import SCHEMA

    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()

    await record_spend(db_path, alias="k1", tokens=100, model="gpt-4", provider="openai")

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT tokens, model, provider FROM spend_log WHERE key_alias = ?", ("k1",)) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 100
    assert row[1] == "gpt-4"
    assert row[2] == "openai"
