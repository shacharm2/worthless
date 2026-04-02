"""Shared test fixtures for Worthless."""

from __future__ import annotations

import json

import os

import pytest
from cryptography.fernet import Fernet
from hypothesis import HealthCheck, settings

# Suppress differing_executors health check ONLY when running under mutmut.
# Mutmut runs tests from its mutants/ directory with a different rootdir,
# which triggers this check spuriously.  In normal test runs, the check
# remains active to catch real working-directory issues.
if os.environ.get("MUTANT_UNDER_TEST"):
    settings.register_profile(
        "mutmut", suppress_health_check=[HealthCheck.differing_executors]
    )
    settings.load_profile("mutmut")

# CI-fast profile: cap Hypothesis examples for speed in parallel/CI runs.
# Activate with: HYPOTHESIS_PROFILE=ci-fast
settings.register_profile("ci-fast", max_examples=50)

if os.environ.get("HYPOTHESIS_PROFILE") == "ci-fast":
    settings.load_profile("ci-fast")

from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.crypto import SplitResult, split_key
from worthless.storage.repository import ShardRepository, StoredShard

from tests.helpers import fake_anthropic_key, fake_openai_key


def make_repo(home: WorthlessHome) -> ShardRepository:
    """Build a ShardRepository from a WorthlessHome (test helper)."""
    return ShardRepository(str(home.db_path), home.fernet_key)


# ------------------------------------------------------------------
# CLI bootstrap fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def home_dir(tmp_path) -> WorthlessHome:
    """Bootstrap a fresh WorthlessHome in tmp_path."""
    return ensure_home(tmp_path / ".worthless")


@pytest.fixture()
def home_with_key(home_dir: WorthlessHome) -> WorthlessHome:
    """Home with one enrolled key (openai)."""
    import asyncio

    key = fake_openai_key()
    sr = split_key(key.encode())
    try:
        alias = "openai-a1b2c3d4"
        shard_a_path = home_dir.shard_a_dir / alias
        fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, bytes(sr.shard_a))
        finally:
            os.close(fd)

        repo = ShardRepository(str(home_dir.db_path), home_dir.fernet_key)
        asyncio.run(repo.initialize())
        stored = StoredShard(
            shard_b=bytearray(sr.shard_b),
            commitment=bytearray(sr.commitment),
            nonce=bytearray(sr.nonce),
            provider="openai",
        )
        asyncio.run(
            repo.store_enrolled(
                alias,
                stored,
                var_name="OPENAI_API_KEY",
                env_path="/tmp/.env",
            )
        )
    finally:
        sr.zero()
    return home_dir


# ------------------------------------------------------------------
# Adapter-layer fixtures (Phase 2)
# ------------------------------------------------------------------


@pytest.fixture
def sample_openai_body() -> bytes:
    """A minimal OpenAI chat completion request body."""
    return json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}).encode()


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
def sample_api_key() -> bytearray:
    """A fake API key bytearray for adapter tests (SR-01: no immutable secrets)."""
    return bytearray(b"sk-test-fake-key-1234567890")


@pytest.fixture
def mock_openai_sse_chunks() -> list[bytes]:
    """Realistic OpenAI SSE chunks."""
    return [
        b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":" world"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]


@pytest.fixture
def mock_anthropic_sse_chunks() -> list[bytes]:
    """Realistic Anthropic SSE chunks."""
    return [
        b'event: content_block_delta\ndata: {"type":"content_block_delta",'
        b'"delta":{"type":"text_delta","text":"Hello"}}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta",'
        b'"delta":{"type":"text_delta","text":" world"}}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]


# ------------------------------------------------------------------
# Crypto-layer fixtures (Phase 1)
# ------------------------------------------------------------------


@pytest.fixture()
def sample_api_key_bytes() -> bytes:
    """A realistic-length API key for crypto tests."""
    return b"sk-test-key-1234567890abcdef"


@pytest.fixture()
def sample_long_key() -> bytes:
    """A 64-byte key for testing longer keys."""
    return b"sk-long-" + b"A" * 56


def assert_zeroed(buf: bytearray) -> None:
    """Assert every byte in *buf* is zero."""
    assert all(b == 0 for b in buf), f"Buffer not zeroed: {buf[:8].hex()}..."


def write_shard_a(home: WorthlessHome, alias: str, data: bytes) -> Path:
    """Write a shard_a file with 0o600 perms. Returns the file path."""
    shard_a_path = home.shard_a_dir / alias
    fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return shard_a_path


# ------------------------------------------------------------------
# Storage-layer fixtures (Phase 1)
# ------------------------------------------------------------------


@pytest.fixture()
def tmp_db_path(tmp_path) -> str:
    """Temporary SQLite database path."""
    return str(tmp_path / "test.db")


@pytest.fixture()
def fernet_key() -> bytes:
    """A freshly generated Fernet key."""
    return Fernet.generate_key()


@pytest.fixture()
def sample_split_result(sample_api_key_bytes: bytes) -> SplitResult:
    """A real SplitResult produced from the sample API key."""
    return split_key(sample_api_key_bytes)


def stored_shard_from_split(sr: SplitResult, provider: str = "openai") -> StoredShard:
    """Build a StoredShard from a SplitResult (wrapping in bytearray per SR-01)."""
    return StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider=provider,
    )


@pytest.fixture()
async def repo(tmp_db_path: str, fernet_key: bytes) -> ShardRepository:
    """An initialized ShardRepository backed by a temp database."""
    r = ShardRepository(tmp_db_path, fernet_key)
    await r.initialize()
    return r
