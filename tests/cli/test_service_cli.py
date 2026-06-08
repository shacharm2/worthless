"""CLI tests for ``worthless service`` — backends mocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.commands.service._common import ServiceState, ServiceStatus
from worthless.cli.errors import ErrorCode, WorthlessError

runner = CliRunner()


@pytest.fixture()
def home_dir(tmp_path: Path) -> Path:
    base = tmp_path / ".worthless"
    base.mkdir()
    (base / "fernet.key").write_bytes(b"x" * 32)
    return base


class TestServiceInstall:
    def test_install_success_json(self, home_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        mock_backend = MagicMock()
        mock_backend.plist_path.return_value = home_dir / "dev.worthless.proxy.plist"
        mock_backend.unit_path = MagicMock()  # unused on darwin

        with (
            patch("worthless.cli.commands.service._backend", return_value=mock_backend),
            patch(
                "worthless.cli.commands.service.current_platform_backend_name",
                return_value="launchd",
            ),
            patch(
                "worthless.cli.commands.service.resolve_worthless_binary",
                return_value=Path("/usr/local/bin/worthless"),
            ),
            patch("worthless.cli.commands.service.get_home") as mock_home,
        ):
            mock_home.return_value.base_dir = home_dir
            result = runner.invoke(
                app,
                ["--json", "service", "install", "--yes"],
                env={"WORTHLESS_HOME": str(home_dir)},
            )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["installed"] is True
        assert payload["platform"] == "launchd"
        mock_backend.install.assert_called_once()

    def test_status_not_installed(self, home_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        mock_backend = MagicMock()
        mock_backend.detect_status.return_value = ServiceStatus(
            state=ServiceState.NOT_INSTALLED,
            unit_path=None,
            binary=None,
            port=8787,
            healthy=False,
        )

        with (
            patch("worthless.cli.commands.service._backend", return_value=mock_backend),
            patch(
                "worthless.cli.commands.service.current_platform_backend_name",
                return_value="systemd",
            ),
            patch("worthless.cli.commands.service.get_home") as mock_home,
        ):
            mock_home.return_value.base_dir = home_dir
            result = runner.invoke(
                app,
                ["--json", "service", "status"],
                env={"WORTHLESS_HOME": str(home_dir)},
            )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["state"] == "not_installed"
        assert payload["healthy"] is False

    def test_install_preflight_fails_without_fernet(self, home_dir: Path) -> None:
        mock_backend = MagicMock()
        with (
            patch("worthless.cli.commands.service._backend", return_value=mock_backend),
            patch("worthless.cli.commands.service.get_home") as mock_home,
            patch(
                "worthless.cli.commands.service.preflight_service_install",
                side_effect=WorthlessError(ErrorCode.KEY_NOT_FOUND, "no fernet"),
            ),
        ):
            mock_home.return_value.base_dir = home_dir
            result = runner.invoke(
                app,
                ["service", "install", "--yes"],
                env={"WORTHLESS_HOME": str(home_dir)},
            )
        assert result.exit_code != 0
        mock_backend.install.assert_not_called()

    def test_windows_rejected(self) -> None:
        with patch("worthless.cli.commands.service.fail_if_windows") as mock_fail:
            from worthless.cli.errors import ErrorCode, WorthlessError

            mock_fail.side_effect = WorthlessError(ErrorCode.PLATFORM_UNSUPPORTED, "nope")
            result = runner.invoke(app, ["service", "status"])
        assert result.exit_code != 0
