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
    prefix      TEXT,
    charset     TEXT,
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
    key_alias            TEXT PRIMARY KEY,
    spend_cap            REAL,
    rate_limit_rps       REAL NOT NULL DEFAULT 100.0,
    token_budget_daily   INTEGER,
    token_budget_weekly  INTEGER,
    token_budget_monthly INTEGER,
    time_window          TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
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
CREATE INDEX IF NOT EXISTS idx_spend_log_alias_created ON spend_log (key_alias, created_at);
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

        # WOR-207: Add prefix/charset columns to shards for format-preserving split
        cursor = await db.execute("PRAGMA table_info(shards)")
        shard_columns = {row[1] for row in await cursor.fetchall()}
        _SHARD_MIGRATIONS = {"prefix": "TEXT", "charset": "TEXT"}
        for col_name, col_type in _SHARD_MIGRATIONS.items():
            if col_name not in shard_columns:
                assert col_name in _SHARD_MIGRATIONS, f"unexpected column {col_name}"  # noqa: S101
                # Safe: col_name is from a hardcoded allowlist, not user input
                stmt = f"ALTER TABLE shards ADD COLUMN {col_name} {col_type}"  # noqa: S608
                try:
                    await db.execute(stmt)
                except Exception as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        await db.commit()

        # WOR-183: Add rules engine columns to enrollment_config
        # Guard: table may not exist in very old DBs
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='enrollment_config'"
        )
        if await cursor.fetchone() is None:
            return
        cursor = await db.execute("PRAGMA table_info(enrollment_config)")
        config_columns = {row[1] for row in await cursor.fetchall()}
        new_columns = [
            ("token_budget_daily", "INTEGER"),
            ("token_budget_weekly", "INTEGER"),
            ("token_budget_monthly", "INTEGER"),
            ("time_window", "TEXT"),
        ]
        for col_name, col_type in new_columns:
            if col_name not in config_columns:
                try:
                    await db.execute(
                        f"ALTER TABLE enrollment_config ADD COLUMN {col_name} {col_type}"
                    )
                except Exception as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_spend_log_alias_created "
            "ON spend_log (key_alias, created_at)"
        )
        await db.commit()
