"""Shared test fixtures for the worthless test suite."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def sample_openai_body() -> bytes:
    """A minimal OpenAI chat completion request body."""
    return json.dumps(
        {"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()


@pytest.fixture
def sample_anthropic_body() -> bytes:
    """A minimal Anthropic messages request body."""
    return json.dumps(
        {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode()


@pytest.fixture
def sample_api_key() -> str:
    """A fake API key for testing."""
    return "sk-test-fake-key-1234567890"
