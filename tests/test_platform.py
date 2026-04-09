"""Tests for the platform abstraction module."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import psutil
import pytest

from worthless.cli.platform import (
    IS_WINDOWS,
    check_pid_alive,
    kill_tree,
    popen_platform_kwargs,
    warn_windows_once,
)


# ---------------------------------------------------------------------------
# IS_WINDOWS constant
# ---------------------------------------------------------------------------


class TestIsWindows:
    """IS_WINDOWS reflects the current platform."""

    def test_is_bool(self) -> None:
        assert isinstance(IS_WINDOWS, bool)

    def test_matches_sys_platform(self) -> None:
        assert IS_WINDOWS == (sys.platform == "win32")


# ---------------------------------------------------------------------------
# popen_platform_kwargs
# ---------------------------------------------------------------------------


class TestPopenPlatformKwargs:
    """popen_platform_kwargs returns correct Popen args per platform."""

    def test_unix_detach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", False)
        kwargs = popen_platform_kwargs(detach=True, pass_fds=(5, 6))
        assert kwargs["start_new_session"] is True
        assert kwargs["pass_fds"] == (5, 6)
        assert "creationflags" not in kwargs

    def test_unix_no_detach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", False)
        kwargs = popen_platform_kwargs(detach=False)
        assert "start_new_session" not in kwargs
        assert kwargs.get("pass_fds", ()) == ()

    def test_windows_detach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", True)
        kwargs = popen_platform_kwargs(detach=True, pass_fds=(5,))
        assert "creationflags" in kwargs
        assert kwargs["creationflags"] & 0x8  # DETACHED_PROCESS
        assert kwargs["creationflags"] & 0x08000000  # CREATE_NO_WINDOW
        assert "start_new_session" not in kwargs
        assert "pass_fds" not in kwargs  # not supported on Windows

    def test_windows_no_detach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", True)
        kwargs = popen_platform_kwargs(detach=False)
        assert "creationflags" in kwargs
        assert kwargs["creationflags"] == 0x200  # CREATE_NEW_PROCESS_GROUP
        assert "start_new_session" not in kwargs


# ---------------------------------------------------------------------------
# check_pid_alive
# ---------------------------------------------------------------------------


class TestCheckPidAlive:
    """check_pid_alive delegates to psutil."""

    def test_current_process_alive(self) -> None:
        assert check_pid_alive(os.getpid()) is True

    def test_nonexistent_pid(self) -> None:
        assert check_pid_alive(99999999) is False


# ---------------------------------------------------------------------------
# kill_tree
# ---------------------------------------------------------------------------


class TestKillTree:
    """kill_tree uses psutil for cross-platform process tree kill."""

    def test_terminates_children_then_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        terminated: list[int] = []
        mock_child1 = MagicMock(spec=psutil.Process)
        mock_child1.terminate.side_effect = lambda: terminated.append(111)
        mock_child2 = MagicMock(spec=psutil.Process)
        mock_child2.terminate.side_effect = lambda: terminated.append(222)

        mock_parent = MagicMock(spec=psutil.Process)
        mock_parent.children.return_value = [mock_child1, mock_child2]
        mock_parent.terminate.side_effect = lambda: terminated.append(12345)

        monkeypatch.setattr("worthless.cli.platform.psutil.Process", lambda pid: mock_parent)

        kill_tree(12345)  # default force=False → terminate
        assert terminated == [111, 222, 12345]

    def test_force_kills_children_then_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        killed: list[int] = []
        mock_child1 = MagicMock(spec=psutil.Process)
        mock_child1.kill.side_effect = lambda: killed.append(111)

        mock_parent = MagicMock(spec=psutil.Process)
        mock_parent.children.return_value = [mock_child1]
        mock_parent.kill.side_effect = lambda: killed.append(12345)

        monkeypatch.setattr("worthless.cli.platform.psutil.Process", lambda pid: mock_parent)

        kill_tree(12345, force=True)  # force=True → kill (SIGKILL)
        assert killed == [111, 12345]

    def test_already_dead_no_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "worthless.cli.platform.psutil.Process",
            MagicMock(side_effect=psutil.NoSuchProcess(99999)),
        )
        kill_tree(99999)  # Should not raise

    @pytest.mark.adversarial
    def test_access_denied_raises_permission_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "worthless.cli.platform.psutil.Process",
            MagicMock(side_effect=psutil.AccessDenied(12345)),
        )
        with pytest.raises(PermissionError, match="access denied"):
            kill_tree(12345)

    @pytest.mark.adversarial
    def test_child_access_denied_ignored_parent_still_killed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PermissionError on child is swallowed; parent terminate still attempted."""
        terminated: list[int] = []
        mock_child = MagicMock(spec=psutil.Process)
        mock_child.terminate.side_effect = psutil.AccessDenied(111)

        mock_parent = MagicMock(spec=psutil.Process)
        mock_parent.children.return_value = [mock_child]
        mock_parent.terminate.side_effect = lambda: terminated.append(12345)

        monkeypatch.setattr("worthless.cli.platform.psutil.Process", lambda pid: mock_parent)

        kill_tree(12345)
        assert 12345 in terminated

    def test_parent_dies_during_terminate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_parent = MagicMock(spec=psutil.Process)
        mock_parent.children.return_value = []
        mock_parent.terminate.side_effect = psutil.NoSuchProcess(12345)

        monkeypatch.setattr("worthless.cli.platform.psutil.Process", lambda pid: mock_parent)

        kill_tree(12345)  # Should not raise


# ---------------------------------------------------------------------------
# warn_windows_once
# ---------------------------------------------------------------------------


class TestWarnWindowsOnce:
    """One-shot Windows experimental warning."""

    def test_no_warning_on_unix(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", False)
        import worthless.cli.platform as plat

        plat._warned = False
        warn_windows_once()
        assert capsys.readouterr().err == ""

    def test_warning_emitted_on_windows(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", True)
        monkeypatch.delenv("WORTHLESS_WINDOWS_ACK", raising=False)
        import worthless.cli.platform as plat

        plat._warned = False
        warn_windows_once()
        err = capsys.readouterr().err
        assert "key material may persist" in err.lower()

    def test_warning_only_once(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", True)
        monkeypatch.delenv("WORTHLESS_WINDOWS_ACK", raising=False)
        import worthless.cli.platform as plat

        plat._warned = False
        warn_windows_once()
        warn_windows_once()
        err = capsys.readouterr().err
        assert err.count("key material") == 1

    def test_warning_suppressed_by_quiet(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", True)
        monkeypatch.delenv("WORTHLESS_WINDOWS_ACK", raising=False)
        import worthless.cli.platform as plat

        plat._warned = False
        warn_windows_once(quiet=True)
        assert capsys.readouterr().err == ""

    def test_warning_suppressed_by_env_ack(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", True)
        monkeypatch.setenv("WORTHLESS_WINDOWS_ACK", "1")
        import worthless.cli.platform as plat

        plat._warned = False
        warn_windows_once()
        assert capsys.readouterr().err == ""
