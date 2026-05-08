"""Recovery and state-drift user journeys for WOR-445."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values
from typer.testing import CliRunner

from tests.helpers import fake_key, fake_openai_key
from worthless.cli.app import app


runner = CliRunner(mix_stderr=False)


def _scrubbed_env(home: Path) -> dict[str, str | None]:
    return {
        "WORTHLESS_HOME": str(home),
        "WORTHLESS_DB_PATH": None,
        "WORTHLESS_FERNET_KEY": None,
        "WORTHLESS_FERNET_KEY_PATH": None,
        "WORTHLESS_FERNET_FD": None,
        "WORTHLESS_PORT": None,
        "OPENAI_API_KEY": None,
        "ANTHROPIC_API_KEY": None,
        "OPENAI_BASE_URL": None,
        "ANTHROPIC_BASE_URL": None,
    }


def _invoke(args: list[str], home: Path, **kwargs: object):
    return runner.invoke(app, args, env=_scrubbed_env(home), **kwargs)


def _combined_output(result) -> str:
    return result.stdout + result.stderr


@pytest.mark.user_flow
def test_teammate_handoff_locked_env_without_db_fails_with_hint(tmp_path: Path) -> None:
    """A copied locked `.env` without local DB/keyring material is not recoverable."""
    owner_home = tmp_path / "owner" / ".worthless"
    teammate_home = tmp_path / "teammate" / ".worthless"
    owner_project = tmp_path / "owner-project"
    teammate_project = tmp_path / "teammate-project"
    owner_project.mkdir()
    teammate_project.mkdir()

    owner_env = owner_project / ".env"
    teammate_env = teammate_project / ".env"
    owner_env.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")

    lock = _invoke(["lock", "--env", str(owner_env)], owner_home)
    assert lock.exit_code == 0, _combined_output(lock)
    teammate_env.write_text(owner_env.read_text())

    unlock = _invoke(["unlock", "--env", str(teammate_env)], teammate_home)
    unlock_output = _combined_output(unlock)

    assert unlock.exit_code != 0, unlock_output
    assert "Traceback" not in unlock_output
    assert "OPENAI_API_KEY" in unlock_output
    assert "no enrollment found" in unlock_output.lower()
    assert "re-lock from the original machine" in unlock_output.lower()


@pytest.mark.user_flow
def test_rotation_relock_restores_new_raw_key(tmp_path: Path) -> None:
    """User rotates a raw key in `.env`, re-locks it, and unlock restores the new key."""
    home = tmp_path / ".worthless"
    env_file = tmp_path / ".env"
    old_key = fake_key("sk-proj-", seed="rotation-old")
    new_key = fake_key("sk-proj-", seed="rotation-new")
    env_file.write_text(f"OPENAI_API_KEY={old_key}\n")

    first_lock = _invoke(["lock", "--env", str(env_file)], home)
    assert first_lock.exit_code == 0, _combined_output(first_lock)
    assert dotenv_values(env_file)["OPENAI_API_KEY"] != old_key

    env_file.write_text(f"OPENAI_API_KEY={new_key}\n")
    second_lock = _invoke(["lock", "--env", str(env_file)], home)
    assert second_lock.exit_code == 0, _combined_output(second_lock)
    assert dotenv_values(env_file)["OPENAI_API_KEY"] != new_key

    unlock = _invoke(["unlock", "--env", str(env_file)], home)
    unlock_output = _combined_output(unlock)
    assert unlock.exit_code == 0, unlock_output
    assert "Traceback" not in unlock_output
    assert dotenv_values(env_file)["OPENAI_API_KEY"] == new_key


@pytest.mark.user_flow
def test_multi_project_unlock_keeps_other_project_protected(tmp_path: Path) -> None:
    """Unlocking one project must not restore or corrupt another project."""
    home = tmp_path / ".worthless"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    env_a = project_a / ".env"
    env_b = project_b / ".env"
    key_a = fake_key("sk-proj-", seed="multi-project-a")
    key_b = fake_key("sk-proj-", seed="multi-project-b")
    env_a.write_text(f"OPENAI_API_KEY={key_a}\n")
    env_b.write_text(f"OPENAI_API_KEY={key_b}\n")

    lock_a = _invoke(["lock", "--env", str(env_a)], home)
    assert lock_a.exit_code == 0, _combined_output(lock_a)
    locked_a = dotenv_values(env_a)["OPENAI_API_KEY"]
    assert locked_a != key_a

    lock_b = _invoke(["lock", "--env", str(env_b)], home)
    assert lock_b.exit_code == 0, _combined_output(lock_b)
    locked_b = dotenv_values(env_b)["OPENAI_API_KEY"]
    assert locked_b != key_b

    unlock_a = _invoke(["unlock", "--env", str(env_a)], home)
    unlock_a_output = _combined_output(unlock_a)
    assert unlock_a.exit_code == 0, unlock_a_output
    assert "Traceback" not in unlock_a_output
    assert dotenv_values(env_a)["OPENAI_API_KEY"] == key_a
    assert dotenv_values(env_b)["OPENAI_API_KEY"] == locked_b

    status = _invoke(["status"], home)
    status_output = _combined_output(status)
    assert status.exit_code == 0, status_output
    assert "PROTECTED" in status_output

    unlock_b = _invoke(["unlock", "--env", str(env_b)], home)
    unlock_b_output = _combined_output(unlock_b)
    assert unlock_b.exit_code == 0, unlock_b_output
    assert dotenv_values(env_b)["OPENAI_API_KEY"] == key_b
