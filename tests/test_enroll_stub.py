"""Tests for enroll_stub.py (WOR-74)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from worthless.cli.enroll_stub import enroll_stub
from worthless.storage.repository import ShardRepository
from worthless.crypto.splitter import reconstruct_key

from tests.helpers import fake_openai_key

_TEST_KEY = fake_openai_key()


class TestEnrollStub:
    """Core enroll_stub behaviour."""

    def test_returns_shard_a_bytes(self, tmp_db_path: str, fernet_key: bytes) -> None:
        """enroll_stub returns shard_a bytes for the caller to store."""
        shard_a = asyncio.run(
            enroll_stub("test-alias", _TEST_KEY, "openai", tmp_db_path, fernet_key)
        )
        assert isinstance(shard_a, bytes)
        assert len(shard_a) > 0

    def test_shard_b_stored_in_db(self, tmp_db_path: str, fernet_key: bytes) -> None:
        """enroll_stub stores shard_b in the database."""
        asyncio.run(
            enroll_stub("test-alias", _TEST_KEY, "openai", tmp_db_path, fernet_key)
        )
        repo = ShardRepository(tmp_db_path, fernet_key)
        stored = asyncio.run(repo.initialize() or repo.retrieve("test-alias"))

        async def _get():
            r = ShardRepository(tmp_db_path, fernet_key)
            await r.initialize()
            return await r.retrieve("test-alias")

        stored = asyncio.run(_get())
        assert stored is not None
        assert stored.provider == "openai"

    def test_reconstruct_roundtrip(self, tmp_db_path: str, fernet_key: bytes) -> None:
        """shard_a + shard_b from DB reconstruct the original key."""
        shard_a = asyncio.run(
            enroll_stub("rt-alias", _TEST_KEY, "openai", tmp_db_path, fernet_key)
        )

        async def _get():
            r = ShardRepository(tmp_db_path, fernet_key)
            await r.initialize()
            return await r.retrieve("rt-alias")

        stored = asyncio.run(_get())
        key_buf = reconstruct_key(
            bytearray(shard_a), stored.shard_b, stored.commitment, stored.nonce
        )
        assert key_buf.decode() == _TEST_KEY

    def test_writes_shard_a_file_when_dir_given(
        self, tmp_db_path: str, fernet_key: bytes, tmp_path: Path
    ) -> None:
        """When shard_a_dir is provided, enroll_stub writes the file."""
        shard_a_dir = tmp_path / "shard_a"
        shard_a = asyncio.run(
            enroll_stub(
                "file-alias", _TEST_KEY, "openai",
                tmp_db_path, fernet_key,
                shard_a_dir=str(shard_a_dir),
            )
        )
        shard_a_path = shard_a_dir / "file-alias"
        assert shard_a_path.exists()
        assert shard_a_path.read_bytes() == shard_a

    def test_no_shard_a_file_without_dir(
        self, tmp_db_path: str, fernet_key: bytes, tmp_path: Path
    ) -> None:
        """Without shard_a_dir, no file is written."""
        shard_a_dir = tmp_path / "shard_a"
        asyncio.run(
            enroll_stub("no-file", _TEST_KEY, "openai", tmp_db_path, fernet_key)
        )
        assert not shard_a_dir.exists()

    def test_creates_shard_a_dir_if_missing(
        self, tmp_db_path: str, fernet_key: bytes, tmp_path: Path
    ) -> None:
        """shard_a_dir is created if it doesn't exist."""
        nested = tmp_path / "deep" / "nested" / "shard_a"
        asyncio.run(
            enroll_stub(
                "nested-alias", _TEST_KEY, "openai",
                tmp_db_path, fernet_key,
                shard_a_dir=str(nested),
            )
        )
        assert nested.exists()
        assert (nested / "nested-alias").exists()
