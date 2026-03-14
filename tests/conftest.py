"""Shared test fixtures for Worthless."""

import pytest


@pytest.fixture()
def sample_api_key() -> bytes:
    """A realistic-length API key for tests."""
    return b"sk-test-key-1234567890abcdef"


@pytest.fixture()
def sample_long_key() -> bytes:
    """A 64-byte key for testing longer keys."""
    return b"sk-long-" + b"A" * 56
