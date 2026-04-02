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


    def test_multi_env_unlock_one_keeps_other_hash(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Same key in two .env files: unlock one, other's decoy hash persists."""
        env_a = tmp_path / "a.env"
        env_b = tmp_path / "b.env"
        env_a.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        env_b.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")

        # Lock both
        _lock(env_a, home_dir)
        _lock(env_b, home_dir)

        # Extract decoys
        decoy_a = env_a.read_text().strip().split("=", 1)[1]
        decoy_b = env_b.read_text().strip().split("=", 1)[1]

        repo = _repo(home_dir)
        assert asyncio.run(repo.is_known_decoy(decoy_a)) is True
        assert asyncio.run(repo.is_known_decoy(decoy_b)) is True

        # Unlock only env_a
        result = runner.invoke(
            app,
            ["unlock", "--env", str(env_a)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # env_a's decoy hash gone, env_b's persists
        repo2 = _repo(home_dir)
        assert asyncio.run(repo2.is_known_decoy(decoy_a)) is False
        assert asyncio.run(repo2.is_known_decoy(decoy_b)) is True

        # Original key restored in env_a
        assert _TEST_KEY in env_a.read_text()
        # env_b still has its decoy
        assert decoy_b in env_b.read_text()


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


class TestUnlockMultiEnrollment:
    """Multi-enrollment unlock: same key enrolled from multiple .env files.

    The ambiguity check (env_path=None -> error) is only reachable via the
    library API, not the CLI (--env defaults to .env). Tests call _unlock_alias
    directly to cover this internal contract.
    """

    @pytest.fixture()
    def two_env_files(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create two .env files with the same key."""
        env_a = tmp_path / "a.env"
        env_a.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        env_b = tmp_path / "b.env"
        env_b.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        return env_a, env_b

    def _lock_both(
        self, env_a: Path, env_b: Path, home: WorthlessHome
    ) -> str:
        """Lock same key via two env files, return the alias."""
        _lock(env_a, home)
        _lock(env_b, home)
        shard_a_files = [f.name for f in home.shard_a_dir.iterdir() if f.is_file()]
        assert len(shard_a_files) == 1  # same key -> same alias
        return shard_a_files[0]

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

        # env_b still has decoy
        assert _TEST_KEY not in env_b.read_text()

        # shard_a still exists (one enrollment remains)
        assert (home_dir.shard_a_dir / alias).exists()

        # One enrollment remains in DB
        repo = _repo(home_dir)
        remaining = asyncio.run(repo.list_enrollments(alias))
        assert len(remaining) == 1


class TestUnlockErrorBranches:
    """Error branch coverage for unlock failure paths."""

    def test_unlock_db_retrieve_failure_exits_clean(
        self, home_with_key: WorthlessHome, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception during repo.retrieve -> exit_code=1 with WRTLS."""
        from worthless.storage.repository import ShardRepository

        async def _boom(self, _alias):
            raise Exception("DB corrupt")

        monkeypatch.setattr(
            "worthless.storage.repository.ShardRepository.retrieve", _boom,
        )

        # Find the alias from the enrolled key
        aliases = [f.name for f in home_with_key.shard_a_dir.iterdir() if f.is_file()]
        assert len(aliases) == 1

        result = runner.invoke(
            app,
            ["unlock", "--alias", aliases[0]],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1
        assert "WRTLS" in result.output

    def test_unlock_shard_a_read_failure_exits_clean(
        self, home_with_key: WorthlessHome, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PermissionError reading shard_a file -> exit_code=1."""
        _real_read_bytes = Path.read_bytes

        def _fail_read(self):
            if "shard_a" in str(self):
                raise PermissionError("permission denied")
            return _real_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", _fail_read)

        aliases = [f.name for f in home_with_key.shard_a_dir.iterdir() if f.is_file()]
        assert len(aliases) == 1

        result = runner.invoke(
            app,
            ["unlock", "--alias", aliases[0]],
            env={"WORTHLESS_HOME": str(home_with_key.base_dir)},
        )
        assert result.exit_code == 1

    def test_unlock_no_shard_b_in_db_exits_clean(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """shard_a exists but no shard_b in DB -> error."""
        import os

        # Create shard_a file without DB entry
        alias = "orphan-alias"
        shard_a_path = home_dir.shard_a_dir / alias
        fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, b"fake-shard-data")
        os.close(fd)

        result = runner.invoke(
            app,
            ["unlock", "--alias", alias],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

    def test_unlock_no_enrollment_prints_key(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Unlock alias with no enrollment record prints key to stdout."""
        from worthless.crypto.splitter import split_key
        from worthless.storage.repository import StoredShard
        import os

        alias = "no-enrollment"
        sr = split_key(_TEST_KEY.encode())
        try:
            shard_a_path = home_dir.shard_a_dir / alias
            fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, bytes(sr.shard_a))
            finally:
                os.close(fd)

            # Store shard_b in DB but with no enrollment
            repo = _repo(home_dir)
            stored = StoredShard(
                shard_b=bytearray(sr.shard_b),
                commitment=bytearray(sr.commitment),
                nonce=bytearray(sr.nonce),
                provider="openai",
            )
            asyncio.run(repo.store(alias, stored))
        finally:
            sr.zero()

        result = runner.invoke(
            app,
            ["unlock", "--alias", alias],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        assert _TEST_KEY in result.output


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


class TestListAliasesNoDir:
    """_list_aliases returns [] when shard_a_dir doesn't exist."""

    def test_no_shard_a_dir(self, tmp_path: Path) -> None:
        from worthless.cli.commands.unlock import _list_aliases

        home = WorthlessHome(base_dir=tmp_path / "nonexistent-home")
        assert _list_aliases(home) == []
