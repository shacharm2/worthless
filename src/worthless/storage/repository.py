"""Encrypted shard repository backed by SQLite (STOR-01, STOR-02)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import aiosqlite
from cryptography.fernet import Fernet

from worthless.storage.schema import init_db


class EncryptedShard(NamedTuple):
    """Raw encrypted shard record — no Fernet decryption applied."""

    shard_b_enc: bytes
    commitment: bytes
    nonce: bytes
    provider: str

    def __repr__(self) -> str:
        return (
            f"EncryptedShard(shard_b_enc=<{len(self.shard_b_enc)} bytes>, "
            f"commitment=<{len(self.commitment)} bytes>, "
            f"nonce=<{len(self.nonce)} bytes>, provider={self.provider!r})"
        )


@dataclass
class StoredShard:
    """Decrypted shard record with bytearray fields (SR-01 compliance)."""

    shard_b: bytearray
    commitment: bytearray
    nonce: bytearray
    provider: str

    def __repr__(self) -> str:
        return (
            f"StoredShard(shard_b=<{len(self.shard_b)} bytes>, "
            f"commitment=<{len(self.commitment)} bytes>, "
            f"nonce=<{len(self.nonce)} bytes>, provider={self.provider!r})"
        )


class ShardRepository:
    """Async repository that encrypts Shard B at rest with Fernet.

    Each public method opens its own ``aiosqlite`` connection (simple PoC
    approach -- connection pooling is not needed at this stage).

    .. todo:: Use a persistent connection or pool before production (STOR-01).
    """

    def __init__(self, db_path: str, fernet_key: bytes) -> None:
        self._db_path = db_path
        self._fernet = Fernet(fernet_key)

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        await init_db(self._db_path)

    # ------------------------------------------------------------------
    # Shard CRUD
    # ------------------------------------------------------------------

    async def store(
        self,
        alias: str,
        shard: StoredShard,
    ) -> None:
        """Encrypt *shard.shard_b* with Fernet and INSERT into the shards table.

        Accepts bytearray or bytes for shard_b (converts to bytes for Fernet).
        Raises ``aiosqlite.IntegrityError`` if *alias* already exists.
        """
        shard_b_enc = self._fernet.encrypt(bytes(shard.shard_b))
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO shards (key_alias, shard_b_enc, commitment, nonce, provider) "
                "VALUES (?, ?, ?, ?, ?)",
                (alias, shard_b_enc, bytes(shard.commitment), bytes(shard.nonce), shard.provider),
            )
            await db.commit()

    async def fetch_encrypted(self, alias: str) -> EncryptedShard | None:
        """Return the raw encrypted shard without Fernet decryption, or *None*.

        This enables gate-before-decrypt: the rules engine can evaluate
        before any key material is decrypted (CRYP-05).
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT shard_b_enc, commitment, nonce, provider "
                "FROM shards WHERE key_alias = ?",
                (alias,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return EncryptedShard(
                shard_b_enc=bytes(row["shard_b_enc"]),
                commitment=bytes(row["commitment"]),
                nonce=bytes(row["nonce"]),
                provider=row["provider"],
            )

    def decrypt_shard(self, encrypted: EncryptedShard) -> StoredShard:
        """Fernet-decrypt an :class:`EncryptedShard` into a :class:`StoredShard`.

        All byte fields are wrapped in ``bytearray`` per SR-01.
        """
        shard_b = self._fernet.decrypt(encrypted.shard_b_enc)
        return StoredShard(
            shard_b=bytearray(shard_b),
            commitment=bytearray(encrypted.commitment),
            nonce=bytearray(encrypted.nonce),
            provider=encrypted.provider,
        )

    async def retrieve(self, alias: str) -> StoredShard | None:
        """Decrypt and return a :class:`StoredShard` or *None*.

        Backward-compatible convenience that calls fetch_encrypted + decrypt_shard.
        """
        encrypted = await self.fetch_encrypted(alias)
        if encrypted is None:
            return None
        return self.decrypt_shard(encrypted)

    async def list_keys(self) -> list[str]:
        """Return a list of all enrolled key aliases."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT key_alias FROM shards")
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def set_metadata(self, key: str, value: str) -> None:
        """Upsert a metadata key/value pair."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )
            await db.commit()

    async def get_metadata(self, key: str) -> str | None:
        """Return the metadata value for *key*, or *None*."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None
