"""Native CLI user journeys for WOR-440.

These tests chain real Typer command dispatch against an isolated
``WORTHLESS_HOME`` and project directory. They deliberately assert
user-facing output as well as state changes, because these are product
journeys rather than unit tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values
from typer.testing import CliRunner

from tests.helpers import fake_anthropic_key, fake_openai_key
from tests.user_flows.helpers import scrubbed_cli_env
from worthless.cli.app import app


runner = CliRunner(mix_stderr=False)


def _invoke(args: list[str], home: Path, **kwargs: object):
    return runner.invoke(app, args, env=scrubbed_cli_env(home), **kwargs)


@pytest.mark.user_flow
def test_scrubbed_env_deletes_ambient_worthless_overrides(tmp_path: Path) -> None:
    """Guard the user-flow isolation contract against Click env overlay drift."""
    env = scrubbed_cli_env(tmp_path / ".worthless")
    assert env["WORTHLESS_HOME"] == str(tmp_path / ".worthless")
    assert env["HOME"] == str(tmp_path / "user-home")
    assert env["WORTHLESS_FERNET_KEY_PATH"] is None
    assert env["OPENAI_API_KEY"] is None


def _combined_output(result) -> str:
    return result.stdout + result.stderr


@pytest.mark.user_flow
def test_default_command_yes_detects_and_locks_project_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh user runs bare ``worthless --yes`` in a project with keys.

    The journey should show detected providers, protect the keys, add
    provider base URLs, and avoid leaking raw key material in output.
    """
    home = tmp_path / ".worthless"
    project = tmp_path / "project"
    project.mkdir()
    env_file = project / ".env"
    openai_key = fake_openai_key()
    anthropic_key = fake_anthropic_key()
    env_file.write_text(
        f"OPENAI_API_KEY={openai_key}\n"
        f"ANTHROPIC_API_KEY={anthropic_key}\n"
        "DATABASE_URL=postgres://localhost/db\n"
    )
    monkeypatch.chdir(project)

    def _poll_health(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr("worthless.cli.default_command.start_daemon", lambda *a, **kw: None)
    monkeypatch.setattr("worthless.cli.default_command.poll_health", _poll_health)

    result = _invoke(["--yes"], home)

    output = _combined_output(result)
    assert result.exit_code == 0, output
    assert "OPENAI_API_KEY" in output
    assert "ANTHROPIC_API_KEY" in output
    assert "openai" in output.lower()
    assert "anthropic" in output.lower()
    assert openai_key[8:24] not in output
    assert anthropic_key[12:28] not in output
    assert "2 keys protected" in output
    assert "Proxy healthy" in output

    values = dotenv_values(env_file)
    assert values["OPENAI_API_KEY"] != openai_key
    assert values["ANTHROPIC_API_KEY"] != anthropic_key
    assert values["OPENAI_BASE_URL"] is not None
    assert values["ANTHROPIC_BASE_URL"] is not None


@pytest.mark.user_flow
def test_lock_status_scan_unlock_round_trip_restores_original_key(tmp_path: Path) -> None:
    """User locks a project, checks status/scan, then unlocks the key."""
    home = tmp_path / ".worthless"
    env_file = tmp_path / ".env"
    original_key = fake_openai_key()
    env_file.write_text(f"OPENAI_API_KEY={original_key}\n")

    lock = _invoke(["lock", "--env", str(env_file)], home)
    assert lock.exit_code == 0, _combined_output(lock)
    assert dotenv_values(env_file)["OPENAI_API_KEY"] != original_key

    status = _invoke(["status"], home)
    status_output = _combined_output(status)
    assert status.exit_code == 0, status_output
    assert "Enrolled keys" in status_output
    assert "openai" in status_output
    assert "PROTECTED" in status_output
    assert "Proxy: not running" in status_output

    scan = _invoke(["scan", str(tmp_path)], home)
    scan_output = _combined_output(scan)
    assert scan.exit_code == 0, scan_output
    assert "No API keys found" in scan_output
    assert "Traceback" not in scan_output

    unlock = _invoke(["unlock", "--env", str(env_file)], home)
    unlock_output = _combined_output(unlock)
    assert unlock.exit_code == 0, unlock_output
    assert "Traceback" not in unlock_output
    assert dotenv_values(env_file)["OPENAI_API_KEY"] == original_key

    final_status = _invoke(["status"], home)
    final_status_output = _combined_output(final_status)
    assert final_status.exit_code == 0, final_status_output
    assert "No keys enrolled" in final_status_output


@pytest.mark.user_flow
def test_scan_and_status_empty_states_are_plain_english(tmp_path: Path) -> None:
    """A fresh user with no keys gets readable empty states."""
    home = tmp_path / ".worthless"
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgres://localhost/db\nDEBUG=true\n")

    scan = _invoke(["scan", str(tmp_path)], home)
    scan_output = _combined_output(scan)
    assert scan.exit_code == 0, scan_output
    assert "No API keys found" in scan_output
    assert "Traceback" not in scan_output

    status = _invoke(["status"], home)
    status_output = _combined_output(status)
    assert status.exit_code == 0, status_output
    assert "No keys enrolled" in status_output
    assert "Proxy: not running" in status_output
    assert "Traceback" not in status_output
