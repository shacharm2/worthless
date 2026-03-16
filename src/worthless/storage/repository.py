"""Encrypted shard repository backed by SQLite (STOR-01, STOR-02)."""

from __future__ import annotations

from typing import NamedTuple

import aiosqlite
from cryptography.fernet import Fernet

from worthless.storage.schema import init_db


class StoredShard(NamedTuple):
    """Decrypted shard record returned by the repository."""

    shard_b: bytes
    commitment: bytes
    nonce: bytes
    provider: str


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

        Raises ``aiosqlite.IntegrityError`` if *alias* already exists.
        """
        shard_b_enc = self._fernet.encrypt(shard.shard_b)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO shards (key_alias, shard_b_enc, commitment, nonce, provider) "
                "VALUES (?, ?, ?, ?, ?)",
                (alias, shard_b_enc, shard.commitment, shard.nonce, shard.provider),
            )
            await db.commit()

    async def retrieve(self, alias: str) -> StoredShard | None:
        """Decrypt and return a :class:`StoredShard` or *None*."""
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
            shard_b = self._fernet.decrypt(row["shard_b_enc"])
            return StoredShard(
                shard_b, bytes(row["commitment"]), bytes(row["nonce"]), row["provider"]
            )

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
