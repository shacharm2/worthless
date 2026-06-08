"""Unit tests for the response-model extractor (WOR-696).

Tests the pure parser directly, pinning BOTH provider SSE wire shapes:

- OpenAI Chat Completions: ``data: {"model":"gpt-...","choices":...}`` per chunk
- Anthropic Messages: ``data: {"type":"message_start","message":{"model":"claude-...","..."}}``

The integration test in test_ceiling_floor_and_stream_kills.py covers the
end-to-end OpenAI path through the FastAPI app. This file fills the gap
the spec-vs-implementation review (Jenny) flagged: the Anthropic branch
of ``extract_response_model`` was implemented but never exercised by a
test.
"""

from __future__ import annotations

from worthless.proxy.response_model_audit import extract_response_model


# ---------------------------------------------------------------------------
# extract_response_model — OpenAI shape
# ---------------------------------------------------------------------------


def test_extract_openai_per_chunk_model() -> None:
    """OpenAI emits the model at top level of every streamed chunk."""
    chunk = b'data: {"model":"gpt-4o-mini","choices":[{"delta":{"role":"assistant"}}]}\n\n'
    assert extract_response_model(chunk) == "gpt-4o-mini"


def test_extract_openai_picks_first_data_event_when_chunk_carries_many() -> None:
    """Multiple SSE events in one chunk → first ``model`` field wins."""
    chunk = (
        b'data: {"model":"gpt-5","choices":[{"delta":{"role":"assistant"}}]}\n\n'
        b'data: {"model":"gpt-5","choices":[{"delta":{"content":"hi"}}]}\n\n'
    )
    assert extract_response_model(chunk) == "gpt-5"


# ---------------------------------------------------------------------------
# extract_response_model — Anthropic shape
# ---------------------------------------------------------------------------


def test_extract_anthropic_message_start_nested_model() -> None:
    """Anthropic carries ``model`` one level deeper, under ``message``."""
    chunk = (
        b'data: {"type":"message_start","message":'
        b'{"id":"msg_01","model":"claude-opus-4-5","role":"assistant"}}\n\n'
    )
    assert extract_response_model(chunk) == "claude-opus-4-5"


def test_extract_anthropic_message_delta_then_message_start() -> None:
    """Anthropic streams a few non-model events first; extractor must skip
    forward to the ``message_start`` event."""
    chunk = (
        b'data: {"type":"ping"}\n\n'
        b'data: {"type":"message_start","message":{"id":"msg_99","model":"claude-haiku-4-5"}}\n\n'
    )
    assert extract_response_model(chunk) == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# extract_response_model — robustness contract
# ---------------------------------------------------------------------------


def test_extract_returns_none_on_done_sentinel() -> None:
    """[DONE] is the OpenAI terminal marker; no model, no panic."""
    assert extract_response_model(b"data: [DONE]\n\n") is None


def test_extract_returns_none_on_empty_chunk() -> None:
    assert extract_response_model(b"") is None


def test_extract_returns_none_on_malformed_json() -> None:
    """Junk in a data line must not raise — passthrough invariant."""
    chunk = b"data: {not really json\n\n"
    assert extract_response_model(chunk) is None


def test_extract_returns_none_on_non_utf8_bytes() -> None:
    """Random binary noise (e.g. compressed framing artifacts) → None, no raise."""
    chunk = b"\xff\xfe\x00\x01data: {\xc3\x28"
    # extractor is forgiving (errors="ignore" on decode)
    assert extract_response_model(chunk) in (None, "")


def test_extract_returns_none_when_model_is_not_a_string() -> None:
    """Defensive: a malformed upstream could send ``model: null`` or ``model: 123``."""
    chunk = b'data: {"model":null,"choices":[]}\n\n'
    assert extract_response_model(chunk) is None
    chunk = b'data: {"model":42,"choices":[]}\n\n'
    assert extract_response_model(chunk) is None


def test_extract_returns_none_when_data_is_a_json_array_not_object() -> None:
    """Hypothetical upstream that wraps events in an array → extractor refuses."""
    chunk = b'data: [{"model":"x"}]\n\n'
    assert extract_response_model(chunk) is None
