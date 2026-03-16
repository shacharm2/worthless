"""Tests for encrypted shard storage (STOR-01, STOR-02)."""

from __future__ import annotations

import aiosqlite
import pytest

from worthless.storage.repository import ShardRepository


# ------------------------------------------------------------------
# Roundtrip: store then retrieve
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shard_roundtrip(
    tmp_db_path: str,
    fernet_key: bytes,
    sample_split_result,
) -> None:
    """Store a shard and retrieve it; shard_b must match original."""
    repo = ShardRepository(tmp_db_path, fernet_key)
    await repo.initialize()

    sr = sample_split_result
    await repo.store("alias1", bytes(sr.shard_b), bytes(sr.commitment), bytes(sr.nonce), "openai")

    result = await repo.retrieve("alias1")
    assert result is not None
    shard_b, commitment, nonce, provider = result
    assert shard_b == bytes(sr.shard_b)
    assert commitment == bytes(sr.commitment)
    assert nonce == bytes(sr.nonce)
    assert provider == "openai"


# ------------------------------------------------------------------
# Encryption at rest
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shard_encrypted_at_rest(
    tmp_db_path: str,
    fernet_key: bytes,
    sample_split_result,
) -> None:
    """Raw SQLite column must NOT contain plaintext shard_b."""
    repo = ShardRepository(tmp_db_path, fernet_key)
    await repo.initialize()

    sr = sample_split_result
    plaintext = bytes(sr.shard_b)
    await repo.store("alias1", plaintext, bytes(sr.commitment), bytes(sr.nonce), "openai")

    # Read raw column directly
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("SELECT shard_b_enc FROM shards WHERE key_alias = 'alias1'")
        row = await cursor.fetchone()
        assert row is not None
        raw_enc = row[0]
        assert raw_enc != plaintext, "shard_b stored in plaintext!"


# ------------------------------------------------------------------
# Metadata persistence
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_persistence(
    tmp_db_path: str,
    fernet_key: bytes,
) -> None:
    """Metadata survives close and reopen of repository."""
    repo = ShardRepository(tmp_db_path, fernet_key)
    await repo.initialize()
    await repo.set_metadata("enrolled_at", "2026-03-16")

    # Create a new repository instance (simulates reopen)
    repo2 = ShardRepository(tmp_db_path, fernet_key)
    await repo2.initialize()
    value = await repo2.get_metadata("enrolled_at")
    assert value == "2026-03-16"


# ------------------------------------------------------------------
# Duplicate alias
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_duplicate_alias_raises(
    tmp_db_path: str,
    fernet_key: bytes,
    sample_split_result,
) -> None:
    """Storing the same alias twice raises an error."""
    repo = ShardRepository(tmp_db_path, fernet_key)
    await repo.initialize()

    sr = sample_split_result
    await repo.store("dup", bytes(sr.shard_b), bytes(sr.commitment), bytes(sr.nonce), "openai")

    with pytest.raises(Exception):  # noqa: B017 — IntegrityError or similar
        await repo.store("dup", bytes(sr.shard_b), bytes(sr.commitment), bytes(sr.nonce), "openai")


# ------------------------------------------------------------------
# Non-existent alias
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_nonexistent_returns_none(
    tmp_db_path: str,
    fernet_key: bytes,
) -> None:
    """Retrieving an unknown alias returns None."""
    repo = ShardRepository(tmp_db_path, fernet_key)
    await repo.initialize()

    assert await repo.retrieve("no-such-alias") is None


# ------------------------------------------------------------------
# List enrolled keys
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_enrolled_keys(
    tmp_db_path: str,
    fernet_key: bytes,
    sample_split_result,
) -> None:
    """list_keys returns all enrolled aliases."""
    repo = ShardRepository(tmp_db_path, fernet_key)
    await repo.initialize()

    sr = sample_split_result
    await repo.store("key-a", bytes(sr.shard_b), bytes(sr.commitment), bytes(sr.nonce), "openai")
    await repo.store("key-b", bytes(sr.shard_b), bytes(sr.commitment), bytes(sr.nonce), "anthropic")

    keys = await repo.list_keys()
    assert sorted(keys) == ["key-a", "key-b"]
