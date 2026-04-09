"""SQLite schema and initialisation for encrypted shard storage."""

from __future__ import annotations

import aiosqlite

SCHEMA = """\
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS shards (
    key_alias   TEXT PRIMARY KEY,
    shard_b_enc BLOB NOT NULL,
    commitment  BLOB NOT NULL,
    nonce       BLOB NOT NULL,
    provider    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spend_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key_alias  TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    model      TEXT,
    provider   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS enrollment_config (
    key_alias      TEXT PRIMARY KEY,
    spend_cap      REAL,
    rate_limit_rps REAL NOT NULL DEFAULT 100.0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS enrollments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key_alias  TEXT NOT NULL REFERENCES shards(key_alias) ON DELETE CASCADE,
    var_name   TEXT NOT NULL,
    env_path   TEXT,
    decoy_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(key_alias, var_name, env_path)
);

CREATE INDEX IF NOT EXISTS idx_spend_log_alias ON spend_log (key_alias);
CREATE INDEX IF NOT EXISTS idx_enrollments_alias ON enrollments (key_alias);
CREATE INDEX IF NOT EXISTS idx_enrollments_decoy_hash
    ON enrollments (decoy_hash) WHERE decoy_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_enrollments_null_path
    ON enrollments (key_alias, var_name) WHERE env_path IS NULL;
"""


async def init_db(db_path: str) -> None:
    """Create tables and enable WAL journal mode."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.commit()


async def migrate_db(db_path: str) -> None:
    """Apply forward-only migrations for existing databases."""
    async with aiosqlite.connect(db_path) as db:
        # WOR-182: Prune spend_log entries older than 90 days
        try:
            await db.execute("DELETE FROM spend_log WHERE created_at < datetime('now', '-90 days')")
            await db.commit()
        except Exception:  # noqa: S110 — spend_log may not exist in pre-schema DBs
            pass

        # WOR-31: Add decoy_hash column to enrollments
        cursor = await db.execute("PRAGMA table_info(enrollments)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "decoy_hash" not in columns:
            try:
                await db.execute("ALTER TABLE enrollments ADD COLUMN decoy_hash TEXT")
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_enrollments_decoy_hash "
                    "ON enrollments (decoy_hash) WHERE decoy_hash IS NOT NULL"
                )
                await db.commit()
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
