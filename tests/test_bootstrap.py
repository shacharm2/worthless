"""Tests for bootstrap — first-run ~/.worthless/ initialization."""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from worthless.cli.bootstrap import (
    WorthlessHome,
    _init_db,
    acquire_lock,
    check_stale_lock,
    ensure_home,
)
from worthless.cli.errors import WorthlessError


class TestEnsureHome:
    def test_creates_directory_structure(self, tmp_path: Path):
        # Force file fallback so fernet_key_path exists on disk
        with patch("worthless.cli.keystore.keyring_available", return_value=False):
            home = ensure_home(base_dir=tmp_path / ".worthless")
        assert home.base_dir.exists()
        assert home.shard_a_dir.exists()
        assert home.db_path.exists()
        assert home.fernet_key_path.exists()

    def test_directory_permissions(self, tmp_path: Path):
        home = ensure_home(base_dir=tmp_path / ".worthless")
        mode = home.base_dir.stat().st_mode
        assert stat.S_IMODE(mode) == 0o700

    def test_fernet_key_permissions(self, tmp_path: Path):
        # Force file fallback so fernet.key is written to disk
        with patch("worthless.cli.keystore.keyring_available", return_value=False):
            home = ensure_home(base_dir=tmp_path / ".worthless")
        mode = home.fernet_key_path.stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_idempotent(self, tmp_path: Path):
        base = tmp_path / ".worthless"
        home1 = ensure_home(base_dir=base)
        key1 = home1.fernet_key
        home2 = ensure_home(base_dir=base)
        key2 = home2.fernet_key
        # Key should not change on second call
        assert key1 == key2

    def test_fernet_key_is_valid(self, tmp_path: Path):
        home = ensure_home(base_dir=tmp_path / ".worthless")
        # Should not raise
        f = Fernet(home.fernet_key)
        ct = f.encrypt(b"test")
        assert f.decrypt(ct) == b"test"

    def test_worthless_home_properties(self, tmp_path: Path):
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        assert home.db_path == tmp_path / ".worthless" / "worthless.db"
        assert home.fernet_key_path == tmp_path / ".worthless" / "fernet.key"
        assert home.shard_a_dir == tmp_path / ".worthless" / "shard_a"
        assert home.lock_file == tmp_path / ".worthless" / ".lock-in-progress"

    def test_fernet_key_path_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        custom_path = tmp_path / "secrets" / "fernet.key"
        monkeypatch.setenv("WORTHLESS_FERNET_KEY_PATH", str(custom_path))
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        assert home.fernet_key_path == custom_path

    def test_fernet_key_written_to_custom_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir(mode=0o700)
        custom_path = secrets_dir / "fernet.key"
        monkeypatch.setenv("WORTHLESS_FERNET_KEY_PATH", str(custom_path))

        # Force file fallback so key is written to disk
        with patch("worthless.cli.keystore.keyring_available", return_value=False):
            home = ensure_home(base_dir=tmp_path / ".worthless")
        assert custom_path.exists()
        assert home.fernet_key_path == custom_path
        # Default location should NOT have fernet.key
        assert not (tmp_path / ".worthless" / "fernet.key").exists()

    def test_custom_fernet_path_missing_dir_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "WORTHLESS_FERNET_KEY_PATH", str(tmp_path / "nonexistent" / "fernet.key")
        )
        with pytest.raises(WorthlessError) as exc_info:
            ensure_home(base_dir=tmp_path / ".worthless")
        assert "WORTHLESS_FERNET_KEY_PATH" in str(exc_info.value)
        assert "does not exist" in str(exc_info.value)


class TestInitDbMigration:
    """Regression tests for _init_db forward-only migrations."""

    def test_upgrade_adds_decoy_hash_column(self, tmp_path: Path):
        """_init_db on an old DB (enrollments without decoy_hash) must add the column."""
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        home.base_dir.mkdir(mode=0o700, parents=True)

        # Create old-schema DB without decoy_hash
        conn = sqlite3.connect(str(home.db_path))
        conn.executescript("""
            CREATE TABLE shards (
                key_alias TEXT PRIMARY KEY, shard_b_enc BLOB NOT NULL,
                commitment BLOB NOT NULL, nonce BLOB NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE spend_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, key_alias TEXT NOT NULL,
                tokens INTEGER NOT NULL, model TEXT, provider TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE enrollment_config (
                key_alias TEXT PRIMARY KEY, spend_cap REAL,
                rate_limit_rps REAL NOT NULL DEFAULT 100.0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_alias TEXT NOT NULL REFERENCES shards(key_alias) ON DELETE CASCADE,
                var_name TEXT NOT NULL, env_path TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(key_alias, var_name, env_path)
            );
        """)
        conn.commit()
        conn.close()

        _init_db(home)

        conn = sqlite3.connect(str(home.db_path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(enrollments)").fetchall()}
        conn.close()
        assert "decoy_hash" in columns

    def test_fresh_db_has_decoy_hash(self, tmp_path: Path):
        """_init_db on a fresh DB creates enrollments with decoy_hash."""
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        home.base_dir.mkdir(mode=0o700, parents=True)

        _init_db(home)

        conn = sqlite3.connect(str(home.db_path))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(enrollments)").fetchall()}
        conn.close()
        assert "decoy_hash" in columns

    def test_upgrade_schema_matches_fresh(self, tmp_path: Path):
        """Upgraded DB schema must converge to the same state as a fresh install."""

        def _get_schema(db_path):
            conn = sqlite3.connect(db_path)
            tables = {}
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall():
                cols = {
                    (row[1], row[2])
                    for row in conn.execute(f"PRAGMA table_info({name})").fetchall()
                }
                tables[name] = cols
            indexes = sorted(
                row[1]
                for row in conn.execute(
                    "SELECT * FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if row[1] is not None
            )
            conn.close()
            return tables, indexes

        # Fresh DB
        fresh_home = WorthlessHome(base_dir=tmp_path / "fresh" / ".worthless")
        fresh_home.base_dir.mkdir(mode=0o700, parents=True)
        _init_db(fresh_home)
        fresh_tables, fresh_indexes = _get_schema(str(fresh_home.db_path))

        # Upgraded DB (old schema without decoy_hash)
        upgrade_home = WorthlessHome(base_dir=tmp_path / "upgrade" / ".worthless")
        upgrade_home.base_dir.mkdir(mode=0o700, parents=True)
        conn = sqlite3.connect(str(upgrade_home.db_path))
        conn.executescript("""
            CREATE TABLE shards (
                key_alias TEXT PRIMARY KEY, shard_b_enc BLOB NOT NULL,
                commitment BLOB NOT NULL, nonce BLOB NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE spend_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, key_alias TEXT NOT NULL,
                tokens INTEGER NOT NULL, model TEXT, provider TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE enrollment_config (
                key_alias TEXT PRIMARY KEY, spend_cap REAL,
                rate_limit_rps REAL NOT NULL DEFAULT 100.0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE enrollments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_alias TEXT NOT NULL REFERENCES shards(key_alias) ON DELETE CASCADE,
                var_name TEXT NOT NULL, env_path TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(key_alias, var_name, env_path)
            );
        """)
        conn.commit()
        conn.close()
        _init_db(upgrade_home)
        upgrade_tables, upgrade_indexes = _get_schema(str(upgrade_home.db_path))

        # Schemas must match
        assert fresh_tables == upgrade_tables, (
            f"Column mismatch:\nfresh={fresh_tables}\nupgrade={upgrade_tables}"
        )
        assert fresh_indexes == upgrade_indexes, (
            f"Index mismatch:\nfresh={fresh_indexes}\nupgrade={upgrade_indexes}"
        )


class TestLocking:
    def test_acquire_lock(self, tmp_path: Path):
        home = ensure_home(base_dir=tmp_path / ".worthless")
        with acquire_lock(home):
            assert home.lock_file.exists()
        assert not home.lock_file.exists()

    def test_lock_prevents_double_acquire(self, tmp_path: Path):
        home = ensure_home(base_dir=tmp_path / ".worthless")
        with acquire_lock(home):
            with pytest.raises(WorthlessError) as exc_info:
                with acquire_lock(home):
                    pass  # pragma: no cover
            assert exc_info.value.code.value == 105  # LOCK_IN_PROGRESS

    def test_stale_lock_cleanup(self, tmp_path: Path):
        home = ensure_home(base_dir=tmp_path / ".worthless")
        # Create a lock file and backdate it > 5 minutes
        home.lock_file.touch()
        old_time = time.time() - 400
        os.utime(home.lock_file, (old_time, old_time))
        # Should remove stale lock without error
        check_stale_lock(home)
        assert not home.lock_file.exists()

    def test_fresh_lock_raises(self, tmp_path: Path):
        home = ensure_home(base_dir=tmp_path / ".worthless")
        home.lock_file.touch()
        with pytest.raises(WorthlessError):
            check_stale_lock(home)
