"""Tests for encrypted shard storage (STOR-01, STOR-02)."""

from __future__ import annotations

import aiosqlite
import pytest

from worthless.storage.repository import EncryptedShard, ShardRepository, StoredShard

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


# ------------------------------------------------------------------
# fetch_encrypted + decrypt_shard split (CRYP-05)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_encrypted_returns_encrypted_shard(
    repo: ShardRepository,
    sample_split_result,
) -> None:
    """fetch_encrypted returns an EncryptedShard with raw encrypted bytes."""
    shard = stored_shard_from_split(sample_split_result)
    await repo.store("enc1", shard)

    enc = await repo.fetch_encrypted("enc1")
    assert enc is not None
    assert isinstance(enc, EncryptedShard)
    # The encrypted blob must differ from the plaintext shard_b
    assert enc.shard_b_enc != bytes(shard.shard_b)
    assert enc.provider == "openai"


@pytest.mark.asyncio
async def test_fetch_encrypted_returns_none_for_unknown(
    repo: ShardRepository,
) -> None:
    """fetch_encrypted returns None for an unknown alias."""
    assert await repo.fetch_encrypted("no-such") is None


@pytest.mark.asyncio
async def test_decrypt_shard_returns_bytearray_stored_shard(
    repo: ShardRepository,
    sample_split_result,
) -> None:
    """decrypt_shard takes EncryptedShard and returns StoredShard with bytearray fields."""
    shard = stored_shard_from_split(sample_split_result)
    await repo.store("dec1", shard)

    enc = await repo.fetch_encrypted("dec1")
    assert enc is not None

    result = repo.decrypt_shard(enc)
    assert isinstance(result, StoredShard)
    assert isinstance(result.shard_b, bytearray)
    assert isinstance(result.commitment, bytearray)
    assert isinstance(result.nonce, bytearray)
    assert result.provider == "openai"
    # Content must match original
    assert bytes(result.shard_b) == bytes(shard.shard_b)
    assert bytes(result.commitment) == bytes(shard.commitment)
    assert bytes(result.nonce) == bytes(shard.nonce)


@pytest.mark.asyncio
async def test_retrieve_backward_compat_with_bytearray(
    repo: ShardRepository,
    sample_split_result,
) -> None:
    """retrieve() still works and returns StoredShard with bytearray fields."""
    shard = stored_shard_from_split(sample_split_result)
    await repo.store("compat1", shard)

    result = await repo.retrieve("compat1")
    assert result is not None
    assert isinstance(result.shard_b, bytearray)
    assert isinstance(result.commitment, bytearray)
    assert isinstance(result.nonce, bytearray)
    assert bytes(result.shard_b) == bytes(shard.shard_b)


# ------------------------------------------------------------------
# spend_log index (H-6/H-7)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spend_log_index_exists(tmp_db_path: str) -> None:
    """idx_spend_log_alias index must exist after init_db."""
    from worthless.storage.schema import init_db

    await init_db(tmp_db_path)

    async with aiosqlite.connect(tmp_db_path) as db:
        cursor = await db.execute("PRAGMA index_list(spend_log)")
        indexes = await cursor.fetchall()
        index_names = [row[1] for row in indexes]
        assert "idx_spend_log_alias" in index_names
