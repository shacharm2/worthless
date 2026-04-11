"""Tests for bootstrap error paths — ensure_home failure modes."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from worthless.cli.bootstrap import ensure_home
from worthless.cli.errors import ErrorCode, WorthlessError


class TestEnsureHomeErrorBranches:
    """Error branch coverage for ensure_home bootstrap failures."""

    def test_ensure_home_permission_denied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PermissionError on mkdir -> WorthlessError with BOOTSTRAP_FAILED."""
        _real_mkdir = Path.mkdir

        def _fail_mkdir(self, *args, **kwargs):
            if ".worthless" in str(self):
                raise PermissionError("permission denied")
            return _real_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", _fail_mkdir)

        with pytest.raises(WorthlessError) as exc_info:
            ensure_home(tmp_path / ".worthless")
        assert exc_info.value.code == ErrorCode.BOOTSTRAP_FAILED

    def test_ensure_home_fernet_key_write_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PermissionError writing fernet.key -> WorthlessError with BOOTSTRAP_FAILED."""
        with patch("worthless.cli.keystore._keyring_available", return_value=False):
            _real_open = os.open

            def _fail_fernet_write(path, flags, *args, **kwargs):
                if "fernet.key" in str(path) and (flags & os.O_CREAT):
                    raise PermissionError(13, "Permission denied", path)
                return _real_open(path, flags, *args, **kwargs)

            monkeypatch.setattr(os, "open", _fail_fernet_write)

            with pytest.raises(WorthlessError) as exc_info:
                ensure_home(tmp_path / ".worthless")
            assert exc_info.value.code == ErrorCode.BOOTSTRAP_FAILED

    def test_ensure_home_db_init_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sqlite3.DatabaseError during DB init -> WorthlessError with SHARD_STORAGE_FAILED."""
        _real_connect = sqlite3.connect

        def _fail_connect(path, *args, **kwargs):
            if "worthless.db" in str(path):
                raise sqlite3.DatabaseError("unable to open database file")
            return _real_connect(path, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", _fail_connect)

        with pytest.raises(WorthlessError) as exc_info:
            ensure_home(tmp_path / ".worthless")
        assert exc_info.value.code == ErrorCode.SHARD_STORAGE_FAILED
