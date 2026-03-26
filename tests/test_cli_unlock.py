"""Tests for the unlock CLI command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.storage.repository import ShardRepository

runner = CliRunner()

_TEST_KEY = "sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
_TEST_KEY_2 = "sk-ant-api03-abc123def456ghi789jkl012mno345pqr678stu901vwx"


@pytest.fixture()
def home_dir(tmp_path: Path) -> WorthlessHome:
    """Bootstrap a fresh WorthlessHome in tmp_path."""
    return ensure_home(tmp_path / ".worthless")


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    """Create a .env with a known OpenAI key."""
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
    return env


@pytest.fixture()
def multi_env_file(tmp_path: Path) -> Path:
    """Create a .env with two API keys."""
    env = tmp_path / ".env"
    env.write_text(
        f"OPENAI_API_KEY={_TEST_KEY}\n"
        f"ANTHROPIC_API_KEY={_TEST_KEY_2}\n"
    )
    return env


def _repo(home: WorthlessHome) -> ShardRepository:
    return ShardRepository(str(home.db_path), home.fernet_key)


def _lock(env_file: Path, home: WorthlessHome) -> None:
    """Helper: lock the env file."""
    result = runner.invoke(
        app,
        ["lock", "--env", str(env_file)],
        env={"WORTHLESS_HOME": str(home.base_dir)},
    )
    assert result.exit_code == 0, result.output


class TestUnlockCommand:
    """Tests for `worthless unlock`."""

    def test_round_trip_lock_unlock(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Lock then unlock should restore identical .env content."""
        original = env_file.read_text()
        _lock(env_file, home_dir)

        # .env should be different after lock
        assert env_file.read_text() != original

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output
        assert env_file.read_text() == original

    def test_unlock_specific_alias(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        """Unlock with --alias should only unlock that specific key."""
        _lock(multi_env_file, home_dir)

        # Find aliases
        shard_a_files = list(home_dir.shard_a_dir.iterdir())
        assert len(shard_a_files) == 2

        # Unlock just one
        alias = shard_a_files[0].name
        result = runner.invoke(
            app,
            ["unlock", "--alias", alias, "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Only one shard_a file should remain
        remaining = list(home_dir.shard_a_dir.iterdir())
        assert len(remaining) == 1

    def test_unlock_all_aliases(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        """Unlock without --alias should unlock all enrolled keys."""
        original = multi_env_file.read_text()
        _lock(multi_env_file, home_dir)

        result = runner.invoke(
            app,
            ["unlock", "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output
        assert multi_env_file.read_text() == original

        # All shard_a files should be gone
        assert list(home_dir.shard_a_dir.iterdir()) == []

        # DB should be empty
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []

    def test_unlock_missing_alias_errors(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Unlock with nonexistent alias should exit with error."""
        result = runner.invoke(
            app,
            ["unlock", "--alias", "nonexistent-key", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

    def test_unlock_no_env_prints_key_to_stdout(
        self, home_dir: WorthlessHome, env_file: Path, tmp_path: Path
    ) -> None:
        """Unlock with no .env should print key to stdout as recovery."""
        _lock(env_file, home_dir)

        # Delete the .env to simulate recovery scenario
        env_file.unlink()
        missing_env = tmp_path / "does-not-exist.env"

        result = runner.invoke(
            app,
            ["unlock", "--env", str(missing_env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output
        # Key should appear in output
        assert _TEST_KEY in result.output

    def test_shards_cleaned_up_after_unlock(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """After unlock, shard_a files and DB entries should be removed."""
        _lock(env_file, home_dir)

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        # No shard_a files
        assert list(home_dir.shard_a_dir.iterdir()) == []

        # No DB entries
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []
