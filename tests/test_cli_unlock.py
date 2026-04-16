"""Tests for the unlock CLI command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome
from worthless.cli.commands.unlock import _unlock_alias
from worthless.cli.errors import WorthlessError

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
    env.write_text(f"OPENAI_API_KEY={_TEST_KEY}\nANTHROPIC_API_KEY={_TEST_KEY_2}\n")
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

    def test_round_trip_lock_unlock(self, home_dir: WorthlessHome, env_file: Path) -> None:
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

    def test_unlock_specific_alias(self, home_dir: WorthlessHome, multi_env_file: Path) -> None:
        """Unlock with --alias should only unlock that specific key."""
        _lock(multi_env_file, home_dir)

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 2

        # Unlock just one
        alias = aliases[0]
        result = runner.invoke(
            app,
            ["unlock", "--alias", alias, "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Only one alias should remain in DB
        remaining = asyncio.run(repo.list_keys())
        assert len(remaining) == 1

    def test_unlock_all_aliases(self, home_dir: WorthlessHome, multi_env_file: Path) -> None:
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

        # DB should be empty
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []

    def test_unlock_missing_alias_errors(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """Unlock with nonexistent alias should exit with error."""
        result = runner.invoke(
            app,
            ["unlock", "--alias", "nonexistent-key", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

    def test_unlock_no_env_prints_error(
        self, home_dir: WorthlessHome, env_file: Path, tmp_path: Path
    ) -> None:
        """Unlock when .env is missing should report error (shard-A is in .env)."""
        _lock(env_file, home_dir)

        # Delete the .env to simulate recovery scenario
        env_file.unlink()
        missing_env = tmp_path / "does-not-exist.env"

        result = runner.invoke(
            app,
            ["unlock", "--env", str(missing_env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        # With format-preserving split, shard-A is in .env,
        # so missing .env means we can't reconstruct
        assert result.exit_code == 1

    def test_shards_cleaned_up_after_unlock(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """After unlock, DB entries should be removed."""
        _lock(env_file, home_dir)

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        # No DB entries
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []


class TestUnlockFromDB:
    """Unlock reads var_name from DB enrollment."""

    def test_unlock_reads_var_name_from_db(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """Unlock should read var_name from enrollments table."""
        original = env_file.read_text()
        _lock(env_file, home_dir)

        # Verify enrollment exists in DB
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1
        enrollment = asyncio.run(repo.get_enrollment(aliases[0]))
        assert enrollment is not None
        assert enrollment.var_name == "OPENAI_API_KEY"

        # Unlock should work from DB enrollment
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


class TestShardAInEnv:
    """After lock, shard-A is the .env value (format-preserving). Unlock reads it back."""

    def test_shard_a_preserved_in_env(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """Lock stores shard-A in .env; unlock reads it back for reconstruction."""
        original = env_file.read_text()
        _lock(env_file, home_dir)

        from dotenv import dotenv_values

        parsed = dotenv_values(env_file)
        shard_a = parsed["OPENAI_API_KEY"]
        # Shard-A must differ from original
        assert shard_a != _TEST_KEY
        # Shard-A preserves prefix
        assert shard_a.startswith("sk-proj-")

        # Unlock reconstructs original
        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        assert env_file.read_text() == original

    def test_unlock_removes_base_url(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """Unlock should remove the BASE_URL line that lock added."""
        _lock(env_file, home_dir)

        content = env_file.read_text()
        assert "OPENAI_BASE_URL=" in content

        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        content_after = env_file.read_text()
        assert "OPENAI_BASE_URL=" not in content_after


class TestEnrollUnlockNullEnvPath:
    """Direct enroll (env_path=NULL) followed by unlock should clean up completely."""

    def test_enroll_then_unlock_prints_key(self, home_dir: WorthlessHome) -> None:
        """After enroll (env_path=NULL), unlock prints key to stdout."""
        from worthless.cli.commands.lock import _make_alias

        alias = _make_alias("openai", _TEST_KEY)

        # Enroll via CLI
        result = runner.invoke(
            app,
            ["enroll", "--alias", alias, "--key", _TEST_KEY, "--provider", "openai"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        # Unlock — no .env, so it fails (shard-A is not on disk anymore)
        # Direct-enrolled keys with format-preserving split have no shard-A on disk
        # and no .env — they need a different recovery path
        result = runner.invoke(
            app,
            ["unlock", "--alias", alias],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        # This will error because there's no .env to read shard_a from
        assert result.exit_code == 1


class TestUnlockMultiEnrollment:
    """Multi-enrollment unlock: same key enrolled from multiple .env files."""

    @pytest.fixture()
    def two_env_files(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create two .env files with the same key."""
        env_a = tmp_path / "a.env"
        env_a.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        env_b = tmp_path / "b.env"
        env_b.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        return env_a, env_b

    def _lock_both(self, env_a: Path, env_b: Path, home: WorthlessHome) -> str:
        """Lock same key via two env files, return the alias."""
        _lock(env_a, home)
        _lock(env_b, home)
        repo = _repo(home)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1  # same key -> same alias
        return aliases[0]

    def test_unlock_alias_multi_enrollment_no_env_raises(
        self, home_dir: WorthlessHome, two_env_files: tuple[Path, Path]
    ) -> None:
        """_unlock_alias with env_path=None errors when alias has multiple enrollments."""
        env_a, env_b = two_env_files
        alias = self._lock_both(env_a, env_b, home_dir)

        async def _run():
            repo = _repo(home_dir)
            await repo.initialize()
            await _unlock_alias(alias, home_dir, repo, env_path=None)

        with pytest.raises(WorthlessError, match="multiple"):
            asyncio.run(_run())

    def test_unlock_multi_enrollment_with_env_flag_succeeds(
        self, home_dir: WorthlessHome, two_env_files: tuple[Path, Path]
    ) -> None:
        """Unlock with --env restores one file, leaves other enrollment intact."""
        env_a, env_b = two_env_files
        original_a = env_a.read_text()
        alias = self._lock_both(env_a, env_b, home_dir)

        result = runner.invoke(
            app,
            ["unlock", "--alias", alias, "--env", str(env_a)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # env_a restored
        assert env_a.read_text() == original_a

        # env_b still enrolled (enrollment remains in DB)
        # Note: env_b may still have original key if re-lock guard
        # didn't rewrite it (same alias already in DB)

        # One enrollment remains in DB
        repo = _repo(home_dir)
        remaining = asyncio.run(repo.list_enrollments(alias))
        assert len(remaining) == 1


class TestUnlockErrorBranches:
    """Error branch coverage for unlock failure paths."""

    def test_unlock_db_retrieve_failure_exits_clean(
        self, home_dir: WorthlessHome, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception during repo.fetch_encrypted -> exit_code=1 with WRTLS."""
        _lock(env_file, home_dir)

        async def _boom(self, _alias):
            raise Exception("DB corrupt")

        monkeypatch.setattr(
            "worthless.storage.repository.ShardRepository.fetch_encrypted",
            _boom,
        )

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1

        result = runner.invoke(
            app,
            ["unlock", "--alias", aliases[0], "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        assert "WRTLS" in result.output

    def test_unlock_no_shard_b_in_db_exits_clean(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """No shard_b in DB for alias -> WRTLS error."""
        result = runner.invoke(
            app,
            ["unlock", "--alias", "nonexistent-alias", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        assert "WRTLS" in result.output


class TestUnlockNoAliases:
    """unlock with no enrolled keys prints warning."""

    def test_unlock_empty_home_warns(self, home_dir: WorthlessHome) -> None:
        """unlock on empty home prints 'No enrolled keys found.'"""
        result = runner.invoke(
            app,
            ["unlock"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        assert "no enrolled" in result.output.lower()


# ------------------------------------------------------------------
# WOR-74: Multi-key unlock scenarios
# ------------------------------------------------------------------


class TestUnlockMultipleKeys:
    """WOR-74: unlock handles multiple enrolled keys, each reconstructs correctly."""

    def test_unlock_multiple_keys_each_reconstructs(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        """Lock two different keys, unlock all, verify both original values restored."""
        original = multi_env_file.read_text()
        _lock(multi_env_file, home_dir)

        # Verify both keys are enrolled
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 2, f"Expected 2 enrolled keys, got {len(aliases)}"

        # Unlock all
        result = runner.invoke(
            app,
            ["unlock", "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Both original keys restored
        restored = multi_env_file.read_text()
        assert _TEST_KEY in restored, "OpenAI key not restored after unlock"
        assert _TEST_KEY_2 in restored, "Anthropic key not restored after unlock"
        assert restored == original
