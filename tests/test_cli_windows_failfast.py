"""Native Windows is an unsupported host for the proxy/wrap entry points.

Worthless's process lifecycle, fernet-key transport, and ``kill_tree``
semantics all rely on POSIX primitives (``setsid``, ``os.killpg``, fd
inheritance). The CLI must refuse to start on native Windows with an
actionable message pointing at WSL or Docker, rather than degrading
silently. ``worthless down`` is deliberately exempt so a Windows user
who somehow ended up with a running daemon can still clean up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app

runner = CliRunner()


_EXPECTED_MESSAGE = "Native Windows is not supported"
_EXPECTED_LINK = "github.com/shacharm2/worthless#platforms"


@pytest.fixture()
def fake_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``IS_WINDOWS`` at the definition site so the helper sees ``True``."""
    monkeypatch.setattr("worthless.cli.platform.IS_WINDOWS", True)


@pytest.fixture()
def home_dir(tmp_path: Path) -> Path:
    """Minimal WORTHLESS_HOME for CLI invocations that otherwise need one."""
    base = tmp_path / ".worthless"
    base.mkdir()
    (base / "fernet.key").write_bytes(b"dummykey")
    return base


class TestUpRefusesOnWindows:
    def test_up_foreground(self, fake_windows: None, home_dir: Path) -> None:
        result = runner.invoke(app, ["up"], env={"WORTHLESS_HOME": str(home_dir)})
        assert result.exit_code == 1, result.output
        assert _EXPECTED_MESSAGE in result.output
        assert _EXPECTED_LINK in result.output

    def test_up_daemon(self, fake_windows: None, home_dir: Path) -> None:
        result = runner.invoke(app, ["up", "--daemon"], env={"WORTHLESS_HOME": str(home_dir)})
        assert result.exit_code == 1, result.output
        assert _EXPECTED_MESSAGE in result.output


class TestWrapRefusesOnWindows:
    def test_wrap(self, fake_windows: None, home_dir: Path) -> None:
        result = runner.invoke(
            app,
            ["wrap", "--", "echo", "hi"],
            env={"WORTHLESS_HOME": str(home_dir)},
        )
        assert result.exit_code == 1, result.output
        assert _EXPECTED_MESSAGE in result.output
        assert _EXPECTED_LINK in result.output


class TestDefaultCommandRefusesOnWindows:
    def test_bare_invocation(self, fake_windows: None, home_dir: Path) -> None:
        """``worthless`` with no subcommand triggers the default pipeline and must refuse."""
        result = runner.invoke(app, ["--yes"], env={"WORTHLESS_HOME": str(home_dir)})
        assert result.exit_code == 1, result.output
        assert _EXPECTED_MESSAGE in result.output


class TestDownExemptOnWindows:
    """``worthless down`` is deliberately exempt — users need an escape hatch."""

    def test_down_does_not_refuse(self, fake_windows: None, home_dir: Path) -> None:
        # No proxy running — down should exit 0 ("not running") without
        # invoking the Windows guard.
        result = runner.invoke(app, ["down"], env={"WORTHLESS_HOME": str(home_dir)})
        assert _EXPECTED_MESSAGE not in result.output, (
            "down must not trip the Windows fail-fast guard"
        )
        assert result.exit_code == 0, result.output
