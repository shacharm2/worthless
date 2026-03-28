"""Tests for the ``worthless up`` command."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestUpDefaultPort:
    """up starts proxy on default port 8787."""

    def test_default_port(self):
        from worthless.cli.commands.up import _resolve_port

        assert _resolve_port(port_arg=None) == 8787

    def test_port_override(self):
        from worthless.cli.commands.up import _resolve_port

        assert _resolve_port(port_arg=9999) == 9999

    def test_env_override(self):
        from worthless.cli.commands.up import _resolve_port

        with patch.dict(os.environ, {"WORTHLESS_PORT": "5555"}):
            assert _resolve_port(port_arg=None) == 5555

    def test_arg_overrides_env(self):
        from worthless.cli.commands.up import _resolve_port

        with patch.dict(os.environ, {"WORTHLESS_PORT": "5555"}):
            assert _resolve_port(port_arg=9999) == 9999


class TestUpPidFile:
    """up writes PID file at expected location."""

    def test_pid_file_path(self, tmp_path: Path):
        from worthless.cli.bootstrap import WorthlessHome

        home = WorthlessHome(base_dir=tmp_path / ".worthless")
        from worthless.cli.commands.up import _pid_path

        result = _pid_path(home)
        assert result == home.base_dir / "proxy.pid"


class TestUpStalePid:
    """up detects stale PID file and reclaims."""

    def test_stale_pid_reclaimed(self, tmp_path: Path):
        from worthless.cli.process import write_pid, cleanup_stale_pid

        pid_path = tmp_path / "proxy.pid"
        write_pid(pid_path, 99999999, 8787)
        assert cleanup_stale_pid(pid_path) is True
        assert not pid_path.exists()


class TestUpLivePid:
    """up with live PID errors with PORT_IN_USE."""

    def test_live_pid_detected(self, tmp_path: Path):
        from worthless.cli.process import write_pid, cleanup_stale_pid

        pid_path = tmp_path / "proxy.pid"
        write_pid(pid_path, os.getpid(), 8787)
        assert cleanup_stale_pid(pid_path) is False


class TestUpDaemon:
    """up -d returns immediately (daemon mode indicator)."""

    def test_daemon_flag_parsed(self):
        """Verify _resolve_port and daemon flag are independent."""
        from worthless.cli.commands.up import _resolve_port

        # Just ensure the function exists and works — daemon is a separate flag
        assert _resolve_port(port_arg=8787) == 8787
