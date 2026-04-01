"""Tests for the status CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome

runner = CliRunner(mix_stderr=False)

# home_with_key fixture is in conftest.py


# ---------------------------------------------------------------------------
# Tests: no enrollment
# ---------------------------------------------------------------------------

class TestStatusNoEnrollment:
    """Tests for status with no enrolled keys."""

    def test_status_no_keys_shows_message(self, home_dir: WorthlessHome) -> None:
        """Status with no enrollment shows 'No keys enrolled'."""
        result = runner.invoke(
            app,
            ["status"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        output = (result.stderr + result.stdout).lower()
        assert "no keys enrolled" in output

    def test_status_json_no_keys(self, home_dir: WorthlessHome) -> None:
        """Status --json with no enrollment returns empty keys array."""
        result = runner.invoke(
            app,
            ["--json", "status"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["keys"] == []


# ---------------------------------------------------------------------------
# Tests: with enrollment
# ---------------------------------------------------------------------------

class TestStatusWithKeys:
    """Tests for status with enrolled keys."""

    def test_status_shows_aliases_and_providers(
        self, home_with_key: WorthlessHome
    ) -> None:
        """Status with enrolled key shows alias and provider."""
        result = runner.invoke(
            app,
            ["status"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0
        output = result.stderr + result.stdout
        assert "openai-a1b2c3d4" in output
        assert "openai" in output

    def test_status_json_with_keys(
        self, home_with_key: WorthlessHome
    ) -> None:
        """Status --json outputs valid JSON with keys array."""
        result = runner.invoke(
            app,
            ["--json", "status"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data["keys"], list)
        assert len(data["keys"]) >= 1
        key_entry = data["keys"][0]
        assert "alias" in key_entry
        assert "provider" in key_entry


# ---------------------------------------------------------------------------
# Tests: proxy health
# ---------------------------------------------------------------------------

class TestStatusProxy:
    """Tests for proxy health check in status."""

    def test_status_unreachable_proxy_shows_not_running(
        self, home_with_key: WorthlessHome
    ) -> None:
        """Status with no proxy running shows 'not running'."""
        result = runner.invoke(
            app,
            ["status"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0
        output = result.stderr + result.stdout
        assert "not running" in output.lower()

    def test_status_json_unreachable_proxy(
        self, home_with_key: WorthlessHome
    ) -> None:
        """Status --json with no proxy shows proxy.healthy=false."""
        result = runner.invoke(
            app,
            ["--json", "status"],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["proxy"]["healthy"] is False
        assert data["proxy"]["port"] is None

    def test_status_mock_healthy_proxy(
        self, home_with_key: WorthlessHome, tmp_path: Path
    ) -> None:
        """Status with mock healthy proxy shows 'running'."""
        # Write a PID file to indicate proxy is running on port 18787
        pid_file = home_with_key.base_dir / "proxy.pid"
        pid_file.write_text("99999\n18787\n")

        # Mock httpx.get to return healthy response

        class MockResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "mode": "up"}

        with patch("worthless.cli.commands.status.httpx") as mock_httpx:
            mock_httpx.get.return_value = MockResponse()
            result = runner.invoke(
                app,
                ["status"],
                env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
            )

        assert result.exit_code == 0
        output = result.stderr + result.stdout
        assert "running" in output.lower() or "18787" in output

    def test_status_json_healthy_proxy(
        self, home_with_key: WorthlessHome, tmp_path: Path
    ) -> None:
        """Status --json with mock proxy shows port and mode."""
        pid_file = home_with_key.base_dir / "proxy.pid"
        pid_file.write_text("99999\n18787\n")


        class MockResponse:
            status_code = 200

            def json(self):
                return {"status": "ok", "mode": "up"}

        with patch("worthless.cli.commands.status.httpx") as mock_httpx:
            mock_httpx.get.return_value = MockResponse()
            result = runner.invoke(
                app,
                ["--json", "status"],
                env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
            )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["proxy"]["healthy"] is True
        assert data["proxy"]["port"] == 18787
        assert data["proxy"]["mode"] == "up"
