"""Tests for the ``worthless wrap`` command."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.commands.wrap import (
    _build_child_env,
    _cleanup_proxy,
    _list_enrolled_providers,
    _run_child_and_wait,
)
from worthless.cli.process import create_liveness_pipe

runner = CliRunner()


class TestWrapEnvInjection:
    """wrap injects BASE_URL env vars for enrolled providers."""

    def test_child_env_has_base_url(self, tmp_path: Path):
        """wrap should inject OPENAI_BASE_URL into child environment."""
        child_env = _build_child_env(port=9999, providers=["openai"])
        assert child_env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999"

    def test_child_env_anthropic(self, tmp_path: Path):
        """wrap should inject ANTHROPIC_BASE_URL for anthropic provider."""
        child_env = _build_child_env(port=8888, providers=["anthropic"])
        assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8888"

    def test_child_env_multiple_providers(self):
        """wrap injects env vars for all enrolled providers."""
        child_env = _build_child_env(port=7777, providers=["openai", "anthropic"])
        assert child_env["OPENAI_BASE_URL"] == "http://127.0.0.1:7777"
        assert child_env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:7777"

    def test_child_env_no_session_token(self):
        """Session token should not be in child env (dead code removed)."""
        child_env = _build_child_env(port=9999, providers=["openai"])
        assert "WORTHLESS_SESSION_TOKEN" not in child_env


class TestWrapExitCode:
    """wrap mirrors child exit code."""

    @pytest.mark.integration
    @pytest.mark.timeout(30)
    def test_mirrors_child_exit_code(self, tmp_path: Path):
        """wrap should exit with the child's exit code."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(42)"],
            process_group=0,
        )
        code = _run_child_and_wait(proc)
        assert code == 42

    @pytest.mark.integration
    @pytest.mark.timeout(30)
    def test_mirrors_zero_exit(self, tmp_path: Path):
        """wrap should exit 0 when child exits 0."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            process_group=0,
        )
        code = _run_child_and_wait(proc)
        assert code == 0


class TestWrapNoKeys:
    """wrap errors when no keys are enrolled."""

    def test_no_enrolled_keys_raises(self, tmp_path: Path):
        from worthless.cli.bootstrap import ensure_home

        home = ensure_home(tmp_path / ".worthless")
        providers = _list_enrolled_providers(home)
        assert providers == []


class TestWrapLivenessPipe:
    """wrap creates liveness pipe for proxy death detection."""

    def test_liveness_pipe_created(self):
        read_fd, write_fd = create_liveness_pipe()
        try:
            os.fstat(read_fd)
            os.fstat(write_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)


class TestWrapSpawnProxyFailure:
    """wrap exits 1 and cleans up FDs when spawn_proxy fails."""

    def test_spawn_failure_exit_code(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When spawn_proxy raises, wrap exits 1."""
        def _fail(**_kw):
            raise RuntimeError("bind failed")

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy", _fail,
        )
        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert "proxy" in result.output.lower()


class TestWrapHealthTimeout:
    """wrap cleans up proxy when poll_health times out."""

    def test_health_timeout_cleans_proxy(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When poll_health returns False, proxy is terminated and exit 1."""
        mock_proxy = MagicMock()
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: False,
        )

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert "healthy" in result.output.lower() or "health" in result.output.lower()
        mock_proxy.terminate.assert_called()


class TestWrapChildSpawnFailure:
    """wrap cleans up proxy when child Popen fails."""

    def test_child_spawn_failure_cleans_proxy(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When child Popen raises, proxy is cleaned up and exit 1."""
        mock_proxy = MagicMock()
        mock_proxy.poll.return_value = None
        mock_proxy.wait.return_value = 0

        monkeypatch.setattr(
            "worthless.cli.commands.wrap.spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        monkeypatch.setattr(
            "worthless.cli.commands.wrap.poll_health",
            lambda *_a, **_kw: True,
        )
        def _fail_popen(*_a, **_kw):
            raise FileNotFoundError("No such file")

        monkeypatch.setattr("subprocess.Popen", _fail_popen)

        result = runner.invoke(
            app,
            ["wrap", "--", "nonexistent-binary"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert "child" in result.output.lower() or "start" in result.output.lower()
        mock_proxy.terminate.assert_called()


class TestCleanupProxy:
    """_cleanup_proxy handles already-dead and stuck processes."""

    def test_cleanup_already_dead(self) -> None:
        """No error when proxy is already dead."""
        mock = MagicMock()
        mock.poll.return_value = 0
        _cleanup_proxy(mock)
        mock.terminate.assert_not_called()

    def test_cleanup_timeout_kills(self) -> None:
        """Proxy that doesn't stop gets SIGKILL."""
        mock = MagicMock()
        mock.poll.return_value = None
        # First wait (terminate) times out, second wait (kill) succeeds
        mock.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="proxy", timeout=5),
            None,
        ]
        _cleanup_proxy(mock, timeout=0.01)
        mock.kill.assert_called()


class TestWrapProxyCrashMidSession:
    """wrap warns on stderr when proxy dies while child is still running."""

    def test_proxy_crash_warns_and_child_continues(
        self, home_with_key, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When proxy crashes mid-session, warning is emitted and exit code is 0."""
        proxy_waited = threading.Event()
        warning_written = threading.Event()
        captured_messages: list[str] = []

        # -- Mock proxy: .wait() returns immediately (simulating crash)
        mock_proxy = MagicMock()
        mock_proxy.pid = 99999

        def proxy_wait(**_kw):
            proxy_waited.set()
            return 0

        mock_proxy.wait.side_effect = proxy_wait
        mock_proxy.poll.return_value = 0  # already dead after crash
        mock_proxy.returncode = 0

        # -- Mock child: .poll() returns None (alive) then 0 (done)
        mock_child = MagicMock()
        mock_child.pid = 99998
        poll_values = iter([None, None, None, 0, 0, 0])
        mock_child.poll.side_effect = lambda: next(poll_values, 0)

        def child_wait(**_kw):
            # Let the watcher thread detect proxy crash first
            proxy_waited.wait(timeout=5)
            time.sleep(0.15)
            mock_child.returncode = 0
            return 0

        mock_child.wait.side_effect = child_wait
        mock_child.returncode = 0

        # Replace the sys module reference inside wrap so _watch_proxy
        # writes to our capturing stderr, not the real one (which CliRunner
        # may have replaced).
        import worthless.cli.commands.wrap as wrap_mod

        fake_sys = ModuleType("fake_sys")
        # Copy all attributes from real sys
        for attr in dir(sys):
            try:
                setattr(fake_sys, attr, getattr(sys, attr))
            except (AttributeError, TypeError):
                pass

        class _CapturingStderr:
            def write(self, msg: str) -> int:
                captured_messages.append(msg)
                if "proxy crashed" in msg:
                    warning_written.set()
                return len(msg)

            def flush(self) -> None:
                pass

        fake_sys.stderr = _CapturingStderr()
        monkeypatch.setattr(wrap_mod, "sys", fake_sys)

        # Patch spawn_proxy -> returns mock proxy
        monkeypatch.setattr(
            wrap_mod, "spawn_proxy",
            lambda **_kw: (mock_proxy, 9999),
        )
        # Patch poll_health -> healthy
        monkeypatch.setattr(
            wrap_mod, "poll_health",
            lambda *_a, **_kw: True,
        )
        # Patch forward_signals -> no-op (can't killpg mock PIDs)
        monkeypatch.setattr(
            wrap_mod, "forward_signals",
            lambda **_kw: None,
        )
        # Patch subprocess.Popen -> returns mock child
        monkeypatch.setattr("subprocess.Popen", lambda *_a, **_kw: mock_child)

        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        # Wait for watcher thread to write the warning
        assert warning_written.wait(timeout=5), "Watcher thread did not emit warning"

        assert result.exit_code == 0
        combined = "".join(captured_messages)
        assert "proxy crashed mid-session" in combined
