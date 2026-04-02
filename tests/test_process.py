"""Tests for process lifecycle module — pipe death detection, PID files, signal forwarding."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest


class TestCreateLivenessPipe:
    """Test create_liveness_pipe returns two valid fds."""

    def test_returns_two_fds(self):
        from worthless.cli.process import create_liveness_pipe

        read_fd, write_fd = create_liveness_pipe()
        try:
            # Both should be valid file descriptors
            os.fstat(read_fd)
            os.fstat(write_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_write_end_eof_on_close(self):
        from worthless.cli.process import create_liveness_pipe

        read_fd, write_fd = create_liveness_pipe()
        os.close(write_fd)
        # Reading from read_fd should get EOF (empty bytes)
        data = os.read(read_fd, 1)
        assert data == b""
        os.close(read_fd)


class TestPidFiles:
    """Test write_pid, read_pid, check_pid, cleanup_stale_pid."""

    def test_write_read_roundtrip(self, tmp_path: Path):
        from worthless.cli.process import read_pid, write_pid

        pid_path = tmp_path / "proxy.pid"
        write_pid(pid_path, 12345, 8787)
        result = read_pid(pid_path)
        assert result == (12345, 8787)

    def test_read_missing_file(self, tmp_path: Path):
        from worthless.cli.process import read_pid

        pid_path = tmp_path / "nonexistent.pid"
        assert read_pid(pid_path) is None

    def test_read_corrupt_file(self, tmp_path: Path):
        from worthless.cli.process import read_pid

        pid_path = tmp_path / "corrupt.pid"
        pid_path.write_text("garbage")
        assert read_pid(pid_path) is None

    def test_check_pid_current_process(self):
        from worthless.cli.process import check_pid

        assert check_pid(os.getpid()) is True

    def test_check_pid_nonexistent(self):
        from worthless.cli.process import check_pid

        # PID 99999999 almost certainly doesn't exist
        assert check_pid(99999999) is False

    def test_cleanup_stale_pid_dead_process(self, tmp_path: Path):
        from worthless.cli.process import cleanup_stale_pid, write_pid

        pid_path = tmp_path / "proxy.pid"
        # Use a PID that doesn't exist
        write_pid(pid_path, 99999999, 8787)
        assert cleanup_stale_pid(pid_path) is True
        assert not pid_path.exists()

    def test_cleanup_stale_pid_live_process(self, tmp_path: Path):
        from worthless.cli.process import cleanup_stale_pid, write_pid

        pid_path = tmp_path / "proxy.pid"
        write_pid(pid_path, os.getpid(), 8787)
        assert cleanup_stale_pid(pid_path) is False
        assert pid_path.exists()

    def test_cleanup_stale_pid_missing_file(self, tmp_path: Path):
        from worthless.cli.process import cleanup_stale_pid

        pid_path = tmp_path / "nonexistent.pid"
        assert cleanup_stale_pid(pid_path) is True


class TestDisableCoreDumps:
    """Test disable_core_dumps doesn't raise."""

    def test_no_exception(self):
        from worthless.cli.process import disable_core_dumps

        # Should not raise on any platform
        disable_core_dumps()


class TestForwardSignals:
    """Test signal handler registration (not full signal delivery)."""

    def test_registers_handlers(self):
        from worthless.cli.process import forward_signals

        # Create mock processes
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            old_int = signal.getsignal(signal.SIGINT)
            old_term = signal.getsignal(signal.SIGTERM)

            forward_signals(proxy=proc, child=None)

            # Handlers should have changed
            new_int = signal.getsignal(signal.SIGINT)
            new_term = signal.getsignal(signal.SIGTERM)
            assert new_int != old_int or new_term != old_term
        finally:
            proc.terminate()
            proc.wait()
            # Restore default handlers
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)


@pytest.mark.integration
@pytest.mark.timeout(30)
class TestSpawnProxyIntegration:
    """Integration test: spawn real proxy and check health."""

    def test_spawn_and_health(self, tmp_path: Path):
        """Spawn proxy on random port, poll health, shut down."""
        from worthless.cli.process import poll_health, spawn_proxy

        # Set up minimal WorthlessHome
        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(tmp_path / ".worthless")

        env = {
            "WORTHLESS_DB_PATH": str(home.db_path),
            "WORTHLESS_FERNET_KEY": home.fernet_key.decode(),
            "WORTHLESS_SHARD_A_DIR": str(home.shard_a_dir),
            "WORTHLESS_ALLOW_INSECURE": "true",
        }

        proc, port = spawn_proxy(env, port=0)
        try:
            assert port > 0
            assert proc.poll() is None  # Still running

            healthy = poll_health(port, timeout=15.0)
            assert healthy is True
        finally:
            proc.terminate()
            proc.wait(timeout=5)
