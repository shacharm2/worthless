"""Encrypted shard repository backed by SQLite (STOR-01, STOR-02)."""

from __future__ import annotations

import hashlib
import hmac
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from collections.abc import AsyncIterator
from typing import NamedTuple

import aiosqlite
from cryptography.fernet import Fernet

from worthless.defaults import DEFAULT_SPEND_CAP_TOKENS
from worthless.storage.schema import init_db, migrate_db

from enum import Enum


class _Sentinel(Enum):
    USE_DEFAULT = "USE_DEFAULT"


_USE_DEFAULT = _Sentinel.USE_DEFAULT


class EncryptedShard(NamedTuple):
    """Raw encrypted shard record — no Fernet decryption applied."""

    shard_b_enc: bytes
    commitment: bytes
    nonce: bytes
    provider: str
    prefix: str | None = None
    charset: str | None = None

    def __repr__(self) -> str:
        return (
            f"EncryptedShard(shard_b_enc=<{len(self.shard_b_enc)} bytes>, "
            f"commitment=<{len(self.commitment)} bytes>, "
            f"nonce=<{len(self.nonce)} bytes>, provider={self.provider!r}, "
            f"prefix={self.prefix!r})"
        )


@dataclass
class EnrollmentRecord:
    """A single enrollment binding a key alias to a var name and optional env path.

    ``provider`` is denormalized via JOIN with ``shards`` at load time so
    callers don't need a separate lookup or alias-prefix parsing. The
    canonical source remains ``shards.provider``; this is a read-side
    convenience.
    """

    key_alias: str
    var_name: str
    env_path: str | None = None
    decoy_hash: str | None = field(default=None, repr=False)
    provider: str | None = None


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

    def zero(self) -> None:
        """Zero all cryptographic fields in place (SR-02)."""
        for buf in (self.shard_b, self.commitment, self.nonce):
            buf[:] = b"\x00" * len(buf)


class ShardRepository:
    """Async repository that encrypts Shard B at rest with Fernet.

    Each public method opens its own ``aiosqlite`` connection (simple PoC
    approach -- connection pooling is not needed at this stage).

    .. todo:: Use a persistent connection or pool before production (STOR-01).
    """

    def __init__(self, db_path: str, fernet_key: bytes | bytearray) -> None:
        self._db_path = db_path
        self._fernet_key_bytes = bytearray(fernet_key)  # SR-01: mutable for zeroing
        self._fernet: Fernet | None = Fernet(
            memoryview(self._fernet_key_bytes).tobytes()
        )  # Fernet requires immutable bytes; we zero _fernet_key_bytes on close()
        # Note: Fernet internally stores an immutable copy — unavoidable with
        # the cryptography library. We zero what we control on close().

    def _get_fernet(self) -> Fernet:
        """Return the Fernet instance, raising if closed."""
        if self._fernet is None:
            raise RuntimeError("ShardRepository has been closed")
        return self._fernet

    def close(self) -> None:
        """Zero key material and release the Fernet instance (SR-02)."""
        for i in range(len(self._fernet_key_bytes)):
            self._fernet_key_bytes[i] = 0
        self._fernet = None

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open a connection with foreign keys enabled."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def initialize(self) -> None:
        """Create tables if they don't exist, then run migrations."""
        await init_db(self._db_path)
        await migrate_db(self._db_path)

    def _compute_decoy_hash(self, value: str) -> str:
        """Compute HMAC-SHA256 of *value* keyed with the Fernet key material."""
        if self._fernet is None:
            raise RuntimeError("ShardRepository has been closed")
        return hmac.new(self._fernet_key_bytes, value.encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Shard CRUD
    # ------------------------------------------------------------------

    async def store(
        self,
        alias: str,
        shard: StoredShard,
        prefix: str | None = None,
        charset: str | None = None,
    ) -> None:
        """Encrypt *shard.shard_b* with Fernet and INSERT into the shards table.

        Accepts bytearray or bytes for shard_b (converts to bytes for Fernet).
        Raises ``aiosqlite.IntegrityError`` if *alias* already exists.
        """
        shard_b_enc = self._get_fernet().encrypt(
            memoryview(shard.shard_b).tobytes()
        )  # Fernet requires immutable bytes
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO shards "
                "(key_alias, shard_b_enc, commitment, nonce, provider, prefix, charset) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    alias,
                    shard_b_enc,
                    memoryview(shard.commitment).tobytes(),
                    memoryview(shard.nonce).tobytes(),
                    shard.provider,
                    prefix,
                    charset,
                ),
            )
            await db.commit()

    async def fetch_encrypted(self, alias: str) -> EncryptedShard | None:
        """Return the raw encrypted shard without Fernet decryption, or *None*.

        This enables gate-before-decrypt: the rules engine can evaluate
        before any key material is decrypted (CRYP-05).
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT shard_b_enc, commitment, nonce, provider, prefix, charset "
                "FROM shards WHERE key_alias = ?",
                (alias,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return EncryptedShard(
                shard_b_enc=memoryview(  # nosemgrep: sr01-key-material-not-bytearray
                    row["shard_b_enc"]
                ).tobytes(),
                commitment=memoryview(  # nosemgrep: sr01-key-material-not-bytearray
                    row["commitment"]
                ).tobytes(),
                nonce=memoryview(  # nosemgrep: sr01-key-material-not-bytearray
                    row["nonce"]
                ).tobytes(),
                provider=row["provider"],
                prefix=row["prefix"],
                charset=row["charset"],
            )

    def decrypt_shard(self, encrypted: EncryptedShard) -> StoredShard:
        """Fernet-decrypt an :class:`EncryptedShard` into a :class:`StoredShard`.

        All byte fields are wrapped in ``bytearray`` per SR-01.
        """
        shard_b = self._get_fernet().decrypt(encrypted.shard_b_enc)
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

    async def delete(self, alias: str) -> bool:
        """Delete the shard record for *alias*. Returns True if deleted."""
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM shards WHERE key_alias = ?", (alias,))
            await db.commit()
            return cursor.rowcount > 0

    async def list_keys(self) -> list[str]:
        """Return a list of all enrolled key aliases."""
        async with self._connect() as db:
            cursor = await db.execute("SELECT key_alias FROM shards")
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    async def list_aliases_with_provider(self) -> list[tuple[str, str]]:
        """Return ``(alias, provider)`` pairs for all enrolled keys."""
        async with self._connect() as db:
            cursor = await db.execute("SELECT key_alias, provider FROM shards")
            rows = await cursor.fetchall()
            return [(r[0], r[1]) for r in rows if r[0] and r[1]]

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def set_metadata(self, key: str, value: str) -> None:
        """Upsert a metadata key/value pair."""
        async with self._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )
            await db.commit()

    async def get_metadata(self, key: str) -> str | None:
        """Return the metadata value for *key*, or *None*."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # Enrollment CRUD
    # ------------------------------------------------------------------

    async def store_enrolled(
        self,
        alias: str,
        shard: StoredShard,
        *,
        var_name: str,
        env_path: str | None = None,
        spend_cap: int | None | _Sentinel = _USE_DEFAULT,
        token_budget_daily: int | None = None,
        prefix: str | None = None,
        charset: str | None = None,
    ) -> None:
        """Atomically store a shard, enrollment record, and enrollment config.

        If the shard already exists (same alias), only the enrollment row
        is inserted.  The enrollment_config row uses INSERT OR IGNORE so
        re-enrollment never overwrites a user-modified spend cap.

        *spend_cap* semantics:

        - omitted / ``_USE_DEFAULT`` -> ``DEFAULT_SPEND_CAP_TOKENS``
        - explicit ``None`` -> NULL (unlimited)
        - integer -> that value
        """
        # Resolve sentinel to the concrete default
        effective_cap: int | None
        if spend_cap is _USE_DEFAULT:
            effective_cap = DEFAULT_SPEND_CAP_TOKENS
        else:
            effective_cap = spend_cap  # type: ignore[assignment]  # int | None at this point

        shard_b_enc = self._get_fernet().encrypt(
            memoryview(shard.shard_b).tobytes()
        )  # Fernet requires immutable bytes
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "INSERT OR IGNORE INTO shards "
                "(key_alias, shard_b_enc, commitment, nonce, provider, prefix, charset) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    alias,
                    shard_b_enc,
                    memoryview(shard.commitment).tobytes(),
                    memoryview(shard.nonce).tobytes(),
                    shard.provider,
                    prefix,
                    charset,
                ),
            )
            await db.execute(
                "INSERT OR IGNORE INTO enrollments "
                "(key_alias, var_name, env_path) "
                "VALUES (?, ?, ?)",
                (alias, var_name, env_path),
            )
            await db.execute(
                "INSERT OR IGNORE INTO enrollment_config"
                " (key_alias, spend_cap, token_budget_daily)"
                " VALUES (?, ?, ?)",
                (alias, effective_cap, token_budget_daily),
            )
            await db.commit()

    async def add_enrollment(
        self, alias: str, *, var_name: str, env_path: str | None = None
    ) -> None:
        """Add an enrollment row without touching the shards table.

        Uses INSERT OR IGNORE so duplicate (alias, var_name, env_path) tuples
        are silently ignored.
        """
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO enrollments (key_alias, var_name, env_path) "
                "VALUES (?, ?, ?)",
                (alias, var_name, env_path),
            )
            await db.commit()

    async def get_enrollment(
        self, alias: str, env_path: str | None = None
    ) -> EnrollmentRecord | None:
        """Return the enrollment for *alias*.

        If *env_path* is given, filter by exact match. Otherwise return the
        first enrollment for the alias (useful when only one exists).
        """
        async with self._connect() as db:
            if env_path is None:
                cursor = await db.execute(
                    "SELECT key_alias, var_name, env_path, decoy_hash FROM enrollments "
                    "WHERE key_alias = ? LIMIT 1",
                    (alias,),
                )
            else:
                cursor = await db.execute(
                    "SELECT key_alias, var_name, env_path, decoy_hash FROM enrollments "
                    "WHERE key_alias = ? AND env_path = ?",
                    (alias, env_path),
                )
            row = await cursor.fetchone()
            if row is None:
                return None
            return EnrollmentRecord(
                key_alias=row[0],
                var_name=row[1],
                env_path=row[2],
                decoy_hash=row[3],
            )

    async def find_enrollment_by_location(
        self, var_name: str, env_path: str
    ) -> EnrollmentRecord | None:
        """Return the enrollment for *var_name* + *env_path*, or ``None``."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT key_alias, var_name, env_path, decoy_hash FROM enrollments "
                "WHERE var_name = ? AND env_path = ?",
                (var_name, env_path),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return EnrollmentRecord(
                key_alias=row[0],
                var_name=row[1],
                env_path=row[2],
                decoy_hash=row[3],
            )

    async def list_enrollments(
        self,
        alias: str | None = None,
    ) -> list[EnrollmentRecord]:
        """Return enrollment records, optionally filtered by *alias*.

        LEFT JOIN with ``shards`` denormalizes ``provider`` onto each
        record so callers don't have to look it up separately. Records
        whose alias has no matching shard row keep ``provider=None``.
        """
        async with self._connect() as db:
            if alias is not None:
                cursor = await db.execute(
                    "SELECT e.key_alias, e.var_name, e.env_path, e.decoy_hash, s.provider "
                    "FROM enrollments e LEFT JOIN shards s ON e.key_alias = s.key_alias "
                    "WHERE e.key_alias = ?",
                    (alias,),
                )
            else:
                cursor = await db.execute(
                    "SELECT e.key_alias, e.var_name, e.env_path, e.decoy_hash, s.provider "
                    "FROM enrollments e LEFT JOIN shards s ON e.key_alias = s.key_alias "
                    "ORDER BY e.key_alias"
                )
            rows = await cursor.fetchall()
            return [
                EnrollmentRecord(
                    key_alias=r[0],
                    var_name=r[1],
                    env_path=r[2],
                    decoy_hash=r[3],
                    provider=r[4],
                )
                for r in rows
            ]

    async def delete_enrollment(self, alias: str, env_path: str | None) -> bool:
        """Delete a single enrollment row. Returns True if deleted."""
        async with self._connect() as db:
            if env_path is None:
                cursor = await db.execute(
                    "DELETE FROM enrollments WHERE key_alias = ? AND env_path IS NULL",
                    (alias,),
                )
            else:
                cursor = await db.execute(
                    "DELETE FROM enrollments WHERE key_alias = ? AND env_path = ?",
                    (alias, env_path),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_enrolled(self, alias: str) -> bool:
        """Delete the shard and all enrollments for *alias* (CASCADE).

        Returns True if deleted.
        """
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM shards WHERE key_alias = ?", (alias,))
            await db.commit()
            return cursor.rowcount > 0

    async def revoke_all(self, alias: str) -> bool:
        """Atomically delete all DB records for *alias* in one transaction.

        Deletes spend_log, enrollment_config, and shards (CASCADE to enrollments).
        Returns True if the shard existed.
        """
        async with aiosqlite.connect(self._db_path, isolation_level=None) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("DELETE FROM spend_log WHERE key_alias = ?", (alias,))
            await db.execute("DELETE FROM enrollment_config WHERE key_alias = ?", (alias,))
            cursor = await db.execute("DELETE FROM shards WHERE key_alias = ?", (alias,))
            await db.execute("COMMIT")
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Decoy hash registry (WOR-31)
    # ------------------------------------------------------------------

    async def set_decoy_hash(self, alias: str, env_path: str | None, decoy_value: str) -> None:
        """Store the HMAC-SHA256 hash of *decoy_value* on the enrollment row."""
        h = self._compute_decoy_hash(decoy_value)
        async with self._connect() as db:
            if env_path is None:
                await db.execute(
                    "UPDATE enrollments SET decoy_hash = ? "
                    "WHERE key_alias = ? AND env_path IS NULL",
                    (h, alias),
                )
            else:
                await db.execute(
                    "UPDATE enrollments SET decoy_hash = ? WHERE key_alias = ? AND env_path = ?",
                    (h, alias, env_path),
                )
            await db.commit()

    async def is_known_decoy(self, value: str) -> bool:
        """Return True if *value* matches any stored decoy hash."""
        h = self._compute_decoy_hash(value)
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT 1 FROM enrollments WHERE decoy_hash = ? LIMIT 1",
                (h,),
            )
            return await cursor.fetchone() is not None

    async def all_decoy_hashes(self) -> set[str]:
        """Return the set of all non-NULL decoy_hash values (for batch checks)."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT decoy_hash FROM enrollments WHERE decoy_hash IS NOT NULL"
            )
            return {row[0] for row in await cursor.fetchall()}
