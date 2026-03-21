"""SQLite schema and initialisation for encrypted shard storage."""

from __future__ import annotations

import aiosqlite

SCHEMA = """\
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
"""


async def init_db(db_path: str) -> None:
    """Create tables and enable WAL journal mode."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.commit()
