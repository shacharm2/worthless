"""Tests for the lock and enroll CLI commands."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.storage.repository import ShardRepository

runner = CliRunner()


@pytest.fixture()
def home_dir(tmp_path: Path) -> WorthlessHome:
    """Bootstrap a fresh WorthlessHome in tmp_path."""
    return ensure_home(tmp_path / ".worthless")


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    """Create a .env with a known OpenAI key."""
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234\n")
    return env


@pytest.fixture()
def multi_env_file(tmp_path: Path) -> Path:
    """Create a .env with multiple API keys."""
    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234\n"
        "ANTHROPIC_API_KEY=sk-ant-api03-abc123def456ghi789jkl012mno345pqr678stu901vwx\n"
        "SOME_OTHER=not-a-key\n"
    )
    return env


def _repo(home: WorthlessHome) -> ShardRepository:
    return ShardRepository(str(home.db_path), home.fernet_key)


class TestLockCommand:
    """Tests for `worthless lock`."""

    def test_lock_creates_shards_and_rewrites_env(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Lock should split key, write shard_a, store shard_b in DB, rewrite .env."""
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env={"WORTHLESS_HOME": str(home_dir.base_dir)})
        assert result.exit_code == 0, result.output

        # .env should be rewritten (different from original)
        new_content = env_file.read_text()
        assert "sk-proj-abc123def456ghi789" not in new_content
        # Decoy should still start with sk-proj-
        line = new_content.strip().split("=", 1)[1]
        assert line.startswith("sk-proj-")

        # shard_a file should exist
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if not f.name.endswith(".meta")]
        assert len(shard_a_files) == 1

        # shard_a file should have 0600 permissions
        mode = shard_a_files[0].stat().st_mode & 0o777
        assert mode == 0o600

        # shard_b should be in DB
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1

    def test_lock_no_env_file_exits_error(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Lock with nonexistent .env should exit with error code."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(tmp_path / "nonexistent.env")],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

    def test_lock_no_api_keys_exits_zero(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Lock with .env that has no API keys should print message and exit 0."""
        env = tmp_path / ".env"
        env.write_text("DATABASE_URL=postgres://localhost/db\n")
        result = runner.invoke(
            app,
            ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        assert "No unprotected" in result.output or "no unprotected" in result.output.lower()

    def test_lock_idempotent_skips_enrolled(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Running lock twice should skip already-enrolled keys."""
        result1 = runner.invoke(app, ["lock", "--env", str(env_file)], env={"WORTHLESS_HOME": str(home_dir.base_dir)})
        assert result1.exit_code == 0

        # Second run -- should skip the already-enrolled key
        result2 = runner.invoke(app, ["lock", "--env", str(env_file)], env={"WORTHLESS_HOME": str(home_dir.base_dir)})
        assert result2.exit_code == 0
        # Still only one shard_a file
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if not f.name.endswith(".meta")]
        assert len(shard_a_files) == 1

    def test_lock_prefix_preservation(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Decoy value should preserve prefix and match original length."""
        original = env_file.read_text().strip().split("=", 1)[1]
        original_len = len(original)

        result = runner.invoke(app, ["lock", "--env", str(env_file)], env={"WORTHLESS_HOME": str(home_dir.base_dir)})
        assert result.exit_code == 0

        decoy = env_file.read_text().strip().split("=", 1)[1]
        assert decoy.startswith("sk-proj-")
        assert len(decoy) == original_len

    def test_lock_multiple_keys(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        """Lock should process all API keys in .env."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if not f.name.endswith(".meta")]
        assert len(shard_a_files) == 2

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 2

    def test_lock_acquires_and_releases_lock_file(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Lock file should not exist after command completes."""
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env={"WORTHLESS_HOME": str(home_dir.base_dir)})
        assert result.exit_code == 0
        assert not home_dir.lock_file.exists()

    def test_lock_with_provider_override(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """--provider flag should override auto-detection."""
        env = tmp_path / ".env"
        env.write_text("MY_KEY=sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234\n")
        result = runner.invoke(
            app,
            ["lock", "--env", str(env), "--provider", "anthropic"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        # Check provider stored as anthropic in DB
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1
        stored = asyncio.run(repo.retrieve(aliases[0]))
        assert stored is not None
        assert stored.provider == "anthropic"


class TestEnrollCommand:
    """Tests for `worthless enroll`."""

    def test_enroll_explicit_args(
        self, home_dir: WorthlessHome
    ) -> None:
        """Enroll with explicit alias, key, and provider."""
        result = runner.invoke(
            app,
            [
                "enroll",
                "--alias", "my-test-key",
                "--key", "sk-proj-abc123def456ghi789jkl012mno345pqr678stu901vwx234",
                "--provider", "openai",
            ],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # shard_a file should exist
        assert (home_dir.shard_a_dir / "my-test-key").exists()

        # shard_b should be in DB
        repo = _repo(home_dir)
        stored = asyncio.run(repo.retrieve("my-test-key"))
        assert stored is not None
        assert stored.provider == "openai"
