"""Tests for encrypted shard storage (STOR-01, STOR-02)."""

from __future__ import annotations

import aiosqlite
import pytest

from worthless.storage.repository import ShardRepository

from tests.conftest import stored_shard_from_split


# ------------------------------------------------------------------
# Roundtrip: store then retrieve
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shard_roundtrip(repo: ShardRepository, sample_split_result) -> None:
    """Store a shard and retrieve it; shard_b must match original."""
    shard = stored_shard_from_split(sample_split_result)
    await repo.store("alias1", shard)

    result = await repo.retrieve("alias1")
    assert result is not None
    assert result.shard_b == shard.shard_b
    assert result.commitment == shard.commitment
    assert result.nonce == shard.nonce
    assert result.provider == "openai"


# ------------------------------------------------------------------
# Encryption at rest
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shard_encrypted_at_rest(
    repo: ShardRepository,
    tmp_db_path: str,
    sample_split_result,
) -> None:
    """Raw SQLite column must NOT contain plaintext shard_b."""
    shard = stored_shard_from_split(sample_split_result)
    await repo.store("alias1", shard)

    # Read raw column directly
    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("SELECT shard_b_enc FROM shards WHERE key_alias = 'alias1'")
        row = await cursor.fetchone()
        assert row is not None
        raw_enc = row[0]
        assert raw_enc != shard.shard_b, "shard_b stored in plaintext!"


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
    repo: ShardRepository,
    sample_split_result,
) -> None:
    """Storing the same alias twice raises IntegrityError."""
    shard = stored_shard_from_split(sample_split_result)
    await repo.store("dup", shard)

    with pytest.raises(aiosqlite.IntegrityError):
        await repo.store("dup", shard)


# ------------------------------------------------------------------
# Non-existent alias
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_nonexistent_returns_none(repo: ShardRepository) -> None:
    """Retrieving an unknown alias returns None."""
    assert await repo.retrieve("no-such-alias") is None


# ------------------------------------------------------------------
# List enrolled keys
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_enrolled_keys(
    repo: ShardRepository,
    sample_split_result,
) -> None:
    """list_keys returns all enrolled aliases."""
    shard_a = stored_shard_from_split(sample_split_result, provider="openai")
    shard_b = stored_shard_from_split(sample_split_result, provider="anthropic")
    await repo.store("key-a", shard_a)
    await repo.store("key-b", shard_b)

    keys = await repo.list_keys()
    assert sorted(keys) == ["key-a", "key-b"]
