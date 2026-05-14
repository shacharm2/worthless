"""Native stress user journeys for destructive state transitions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dotenv import dotenv_values

from tests.helpers import fake_key, fake_openai_key
from tests.user_flows.helpers import scrubbed_cli_env
from worthless.cli.app import app


runner = CliRunner(mix_stderr=False)


def _invoke(args: list[str], home: Path, **kwargs: object):
    return runner.invoke(app, args, env=scrubbed_cli_env(home), **kwargs)


def _combined_output(result) -> str:
    return result.stdout + result.stderr


@pytest.mark.user_flow
def test_lock_rewrite_refusal_leaves_env_and_status_recoverable(tmp_path: Path) -> None:
    """If the final `.env` rewrite is refused, the user is not left half-protected."""
    home = tmp_path / ".worthless"
    project = tmp_path / "project"
    project.mkdir()
    env_file = project / ".env"
    original_key = fake_openai_key()
    original_content = f"OPENAI_API_KEY={original_key}\n"
    env_file.write_text(original_content)

    # A hardlink makes safe_rewrite's path-identity gate refuse destructive
    # rewrites. This simulates a real mishap where the target is unsafe after
    # lock already planned DB writes.
    os.link(env_file, project / ".env.link")

    lock = _invoke(["lock", "--env", str(env_file)], home)
    lock_output = _combined_output(lock)
    assert lock.exit_code != 0, lock_output
    assert "Traceback" not in lock_output
    assert env_file.read_text() == original_content

    status = _invoke(["status"], home)
    status_output = _combined_output(status)
    assert status.exit_code == 0, status_output
    assert "No keys enrolled" in status_output
    assert "PROTECTED" not in status_output

    scan = _invoke(["scan", str(env_file)], home)
    scan_output = _combined_output(scan)
    assert scan.exit_code != 0, scan_output
    assert "OPENAI_API_KEY" in scan_output
    assert original_key[-6:] not in scan_output


@pytest.mark.user_flow
def test_unlock_tampered_locked_env_fails_without_destroying_state(tmp_path: Path) -> None:
    """If a locked value is edited, unlock refuses and preserves the evidence."""
    home = tmp_path / ".worthless"
    env_file = tmp_path / ".env"
    original_key = fake_openai_key()
    env_file.write_text(f"OPENAI_API_KEY={original_key}\n")

    lock = _invoke(["lock", "--env", str(env_file)], home)
    lock_output = _combined_output(lock)
    assert lock.exit_code == 0, lock_output
    locked_value = dotenv_values(env_file)["OPENAI_API_KEY"]
    assert locked_value != original_key

    tampered_value = fake_key("sk-proj-", seed="tampered-locked-env")
    assert tampered_value not in {original_key, locked_value}
    env_file.write_text(f"OPENAI_API_KEY={tampered_value}\n")

    unlock = _invoke(["unlock", "--env", str(env_file)], home)
    unlock_output = _combined_output(unlock)

    assert unlock.exit_code != 0, unlock_output
    assert "Traceback" not in unlock_output
    assert dotenv_values(env_file)["OPENAI_API_KEY"] == tampered_value
    assert any(
        phrase in unlock_output.lower()
        for phrase in (
            "tampered",
            "does not match",
            "commitment",
            "modified after lock",
            "shard mismatch",
        )
    ), unlock_output

    status = _invoke(["status"], home)
    status_output = _combined_output(status)
    assert status.exit_code == 0, status_output
    assert "PROTECTED" in status_output
