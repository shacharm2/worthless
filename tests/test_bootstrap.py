"""Tests for bootstrap — first-run ~/.worthless/ initialization."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest


class TestEnsureHome:
    def test_creates_directory_structure(self, tmp_path: Path):
        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(base_dir=tmp_path / ".worthless")
        assert home.base_dir.exists()
        assert home.shard_a_dir.exists()
        assert home.db_path.exists()
        assert home.fernet_key_path.exists()

    def test_directory_permissions(self, tmp_path: Path):
        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(base_dir=tmp_path / ".worthless")
        mode = home.base_dir.stat().st_mode
        assert stat.S_IMODE(mode) == 0o700

    def test_fernet_key_permissions(self, tmp_path: Path):
        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(base_dir=tmp_path / ".worthless")
        mode = home.fernet_key_path.stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_idempotent(self, tmp_path: Path):
        from worthless.cli.bootstrap import ensure_home

        base = tmp_path / ".worthless"
        home1 = ensure_home(base_dir=base)
        key1 = home1.fernet_key
        home2 = ensure_home(base_dir=base)
        key2 = home2.fernet_key
        # Key should not change on second call
        assert key1 == key2

    def test_fernet_key_is_valid(self, tmp_path: Path):
        from cryptography.fernet import Fernet

        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(base_dir=tmp_path / ".worthless")
        # Should not raise
        f = Fernet(home.fernet_key)
        ct = f.encrypt(b"test")
        assert f.decrypt(ct) == b"test"

    def test_worthless_home_properties(self, tmp_path: Path):
        from worthless.cli.bootstrap import WorthlessHome

        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        assert home.db_path == tmp_path / ".worthless" / "worthless.db"
        assert home.fernet_key_path == tmp_path / ".worthless" / "fernet.key"
        assert home.shard_a_dir == tmp_path / ".worthless" / "shard_a"
        assert home.lock_file == tmp_path / ".worthless" / ".lock-in-progress"


class TestLocking:
    def test_acquire_lock(self, tmp_path: Path):
        from worthless.cli.bootstrap import WorthlessHome, acquire_lock, ensure_home

        home = ensure_home(base_dir=tmp_path / ".worthless")
        with acquire_lock(home):
            assert home.lock_file.exists()
        assert not home.lock_file.exists()

    def test_lock_prevents_double_acquire(self, tmp_path: Path):
        from worthless.cli.bootstrap import acquire_lock, ensure_home
        from worthless.cli.errors import WorthlessError

        home = ensure_home(base_dir=tmp_path / ".worthless")
        with acquire_lock(home):
            with pytest.raises(WorthlessError) as exc_info:
                with acquire_lock(home):
                    pass  # pragma: no cover
            assert exc_info.value.code.value == 105  # LOCK_IN_PROGRESS

    def test_stale_lock_cleanup(self, tmp_path: Path):
        from worthless.cli.bootstrap import check_stale_lock, ensure_home

        home = ensure_home(base_dir=tmp_path / ".worthless")
        # Create a lock file and backdate it > 5 minutes
        home.lock_file.touch()
        old_time = time.time() - 400
        os.utime(home.lock_file, (old_time, old_time))
        # Should remove stale lock without error
        check_stale_lock(home)
        assert not home.lock_file.exists()

    def test_fresh_lock_raises(self, tmp_path: Path):
        from worthless.cli.bootstrap import check_stale_lock, ensure_home
        from worthless.cli.errors import WorthlessError

        home = ensure_home(base_dir=tmp_path / ".worthless")
        home.lock_file.touch()
        with pytest.raises(WorthlessError):
            check_stale_lock(home)
