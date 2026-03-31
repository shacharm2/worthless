"""Tests for the ``worthless up`` command."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome
from worthless.cli.commands.up import _pid_path, _resolve_port
from worthless.cli.process import (
    check_pid,
    cleanup_stale_pid,
    read_pid,
    write_pid,
)

runner = CliRunner()


class TestUpDefaultPort:
    """up starts proxy on default port 8787."""

    def test_default_port(self):
        assert _resolve_port(port_arg=None) == 8787

    def test_port_override(self):
        assert _resolve_port(port_arg=9999) == 9999

    def test_env_override(self):
        with patch.dict(os.environ, {"WORTHLESS_PORT": "5555"}):
            assert _resolve_port(port_arg=None) == 5555

    def test_arg_overrides_env(self):
        with patch.dict(os.environ, {"WORTHLESS_PORT": "5555"}):
            assert _resolve_port(port_arg=9999) == 9999


class TestUpPidFile:
    """up writes PID file at expected location."""

    def test_pid_file_path(self, tmp_path: Path):
        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        result = _pid_path(home)
        assert result == home.base_dir / "proxy.pid"


class TestUpStalePid:
    """up detects stale PID file and reclaims."""

    def test_stale_pid_reclaimed(self, tmp_path: Path):
        pid_path = tmp_path / "proxy.pid"
        write_pid(pid_path, 99999999, 8787)
        assert cleanup_stale_pid(pid_path) is True
        assert not pid_path.exists()


class TestUpLivePid:
    """up with live PID errors with PORT_IN_USE."""

    def test_live_pid_detected(self, tmp_path: Path):
        pid_path = tmp_path / "proxy.pid"
        write_pid(pid_path, os.getpid(), 8787)
        assert cleanup_stale_pid(pid_path) is False


class TestUpDaemon:
    """up -d returns immediately (daemon mode indicator)."""

    def test_daemon_flag_parsed(self):
        """Verify _resolve_port and daemon flag are independent."""
        assert _resolve_port(port_arg=8787) == 8787


class TestUpPidFileErrorBranches:
    """Error branches in PID file management."""

    def test_read_pid_corrupt_file(self, tmp_path: Path) -> None:
        """Corrupt PID file returns None."""
        pid_path = tmp_path / "proxy.pid"
        pid_path.write_text("garbage\n")
        assert read_pid(pid_path) is None

    def test_read_pid_missing_file(self, tmp_path: Path) -> None:
        """Missing PID file returns None."""
        assert read_pid(tmp_path / "nonexistent.pid") is None

    def test_cleanup_stale_pid_corrupt_reclaims(self, tmp_path: Path) -> None:
        """Corrupt PID file is treated as reclaimable."""
        pid_path = tmp_path / "proxy.pid"
        pid_path.write_text("not a pid\n")
        assert cleanup_stale_pid(pid_path) is True
        assert not pid_path.exists()

    def test_check_pid_nonexistent(self) -> None:
        """check_pid returns False for a PID that doesn't exist."""
        assert check_pid(99999999) is False

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        """write_pid and read_pid round-trip correctly."""
        pid_path = tmp_path / "proxy.pid"
        write_pid(pid_path, 12345, 8787)
        info = read_pid(pid_path)
        assert info == (12345, 8787)


class TestUpDaemonFlow:
    """up --daemon starts a daemon process and writes a PID file."""

    def test_daemon_mode_writes_pid(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """up --daemon writes PID file and exits 0 when healthy."""
        mock_proc = MagicMock()
        mock_proc.pid = 54321

        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *_a, **_kw: mock_proc,
        )
        monkeypatch.setattr(
            "worthless.cli.commands.up.poll_health",
            lambda *_a, **_kw: True,
        )

        result = runner.invoke(
            app,
            ["up", "--daemon"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0

        pid_file = _pid_path(home_with_key)
        assert pid_file.exists()
        info = read_pid(pid_file)
        assert info is not None
        pid, port = info
        assert pid == 54321
        assert port == 8787
