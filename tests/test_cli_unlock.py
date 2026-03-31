"""Tests for the unlock CLI command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome

from tests.conftest import make_repo as _repo
from tests.helpers import fake_anthropic_key, fake_openai_key

runner = CliRunner()

_TEST_KEY = fake_openai_key()
_TEST_KEY_2 = fake_anthropic_key()


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

        # Find aliases (exclude .meta files)
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert len(shard_a_files) == 2

        # Unlock just one
        alias = shard_a_files[0].name
        result = runner.invoke(
            app,
            ["unlock", "--alias", alias, "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Only one shard_a file should remain (plus its .meta)
        remaining = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
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


class TestUnlockFromDB:
    """Unlock reads var_name from DB enrollment, not .meta file."""

    def test_unlock_reads_var_name_from_db(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Unlock should read var_name from enrollments table, not .meta."""
        original = env_file.read_text()
        _lock(env_file, home_dir)

        # Verify NO .meta files exist (new behavior)
        meta_files = [f for f in home_dir.shard_a_dir.iterdir() if f.name.endswith(".meta")]
        assert meta_files == [], f"Lock should not create .meta files, found: {meta_files}"

        # Verify enrollment exists in DB
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1
        enrollment = asyncio.run(repo.get_enrollment(aliases[0]))
        assert enrollment is not None
        assert enrollment.var_name == "OPENAI_API_KEY"

        # Unlock should work from DB enrollment (no .meta needed)
        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output
        assert env_file.read_text() == original

    def test_unlock_cleans_up_enrollment_in_db(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """After unlock, enrollment records should be deleted from DB."""
        _lock(env_file, home_dir)

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        repo = _repo(home_dir)
        enrollments = asyncio.run(repo.list_enrollments())
        assert enrollments == []

    def test_unlock_no_meta_files_remain(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """After full lock/unlock cycle, no .meta files should exist."""
        _lock(env_file, home_dir)

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        # shard_a_dir should be completely empty
        remaining = list(home_dir.shard_a_dir.iterdir())
        assert remaining == []


class TestDecoyHashClearedOnUnlock:
    """WOR-31: unlock must clear the decoy_hash so is_known_decoy returns False."""

    def test_decoy_hash_cleared_after_unlock(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Lock stores a decoy hash; unlock deletes enrollment so hash is gone."""
        original = env_file.read_text()
        _lock(env_file, home_dir)

        # Read the decoy value written to .env by lock
        locked_text = env_file.read_text()
        assert locked_text != original, "lock should rewrite .env"
        decoy_value: str | None = None
        for line in locked_text.splitlines():
            if line.startswith("OPENAI_API_KEY="):
                decoy_value = line.split("=", 1)[1]
                break
        assert decoy_value is not None, "locked .env should contain OPENAI_API_KEY"
        assert decoy_value != _TEST_KEY, "locked value should be a decoy, not original"

        # Verify decoy hash is stored and is_known_decoy returns True
        repo = _repo(home_dir)
        assert asyncio.run(repo.is_known_decoy(decoy_value)) is True

        # Unlock
        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # After unlock the enrollment row is deleted, so decoy hash is gone
        repo2 = _repo(home_dir)
        assert asyncio.run(repo2.is_known_decoy(decoy_value)) is False

        # Original key restored
        assert env_file.read_text() == original


class TestEnrollUnlockNullEnvPath:
    """Direct enroll (env_path=NULL) followed by unlock should clean up completely."""

    def test_enroll_then_unlock_cleans_up(self, home_dir: WorthlessHome) -> None:
        """After enroll (env_path=NULL), unlock should remove enrollment + shard."""
        from worthless.cli.commands.lock import _make_alias
        from worthless.crypto.splitter import split_key
        from worthless.storage.repository import StoredShard

        alias = _make_alias("openai", _TEST_KEY)
        sr = split_key(_TEST_KEY.encode())
        try:
            # Write shard_a
            import os

            shard_a_path = home_dir.shard_a_dir / alias
            fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, bytes(sr.shard_a))
            finally:
                os.close(fd)

            # Store with env_path=None (direct enroll)
            repo = _repo(home_dir)
            stored = StoredShard(
                shard_b=bytearray(sr.shard_b),
                commitment=bytearray(sr.commitment),
                nonce=bytearray(sr.nonce),
                provider="openai",
            )
            asyncio.run(repo.store_enrolled(alias, stored, var_name=alias, env_path=None))
        finally:
            sr.zero()

        # Unlock the direct-enrolled key
        result = runner.invoke(
            app,
            ["unlock", "--alias", alias],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Key printed to stdout (no .env to restore)
        assert _TEST_KEY in result.output

        # Everything cleaned up
        assert not shard_a_path.exists(), "shard_a file should be deleted"
        repo2 = _repo(home_dir)
        assert asyncio.run(repo2.list_enrollments(alias)) == []
        assert asyncio.run(repo2.list_keys()) == []
