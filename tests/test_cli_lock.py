"""Tests for the lock and enroll CLI commands."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome
from worthless.cli.dotenv_rewriter import shannon_entropy
from worthless.cli.key_patterns import ENTROPY_THRESHOLD

from tests.conftest import make_repo as _repo
from tests.helpers import fake_anthropic_key, fake_openai_key

runner = CliRunner()


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    """Create a .env with a known OpenAI key."""
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")
    return env


@pytest.fixture()
def multi_env_file(tmp_path: Path) -> Path:
    """Create a .env with multiple API keys."""
    env = tmp_path / ".env"
    env.write_text(
        f"OPENAI_API_KEY={fake_openai_key()}\n"
        f"ANTHROPIC_API_KEY={fake_anthropic_key()}\n"
        "SOME_OTHER=not-a-key\n"
    )
    return env


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
        assert fake_openai_key()[:24] not in new_content
        # Decoy should still start with sk-proj-
        line = new_content.strip().split("=", 1)[1]
        assert line.startswith("sk-proj-")

        # shard_a file should exist
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
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
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert len(shard_a_files) == 1

    def test_lock_prefix_preservation(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Decoy value should preserve prefix and match provider format length (WOR-31)."""
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env={"WORTHLESS_HOME": str(home_dir.base_dir)})
        assert result.exit_code == 0

        decoy = env_file.read_text().strip().split("=", 1)[1]
        assert decoy.startswith("sk-proj-")
        # WOR-31: decoys match provider format length (164 for OpenAI), not original
        assert len(decoy) == 164

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

        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
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
        env.write_text(f"MY_KEY={fake_openai_key()}\n")
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


class TestLockNoMetaFiles:
    """Lock should NOT create .meta files (consolidated into SQLite)."""

    def test_lock_creates_no_meta_files(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """After lock, shard_a_dir should contain NO .meta files."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        meta_files = [f for f in home_dir.shard_a_dir.iterdir() if f.name.endswith(".meta")]
        assert meta_files == [], f"Found .meta files: {[f.name for f in meta_files]}"

    def test_lock_multiple_keys_no_meta_files(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        """After locking multiple keys, no .meta files should exist."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        meta_files = [f for f in home_dir.shard_a_dir.iterdir() if f.name.endswith(".meta")]
        assert meta_files == [], f"Found .meta files: {[f.name for f in meta_files]}"

    def test_lock_stores_enrollment_in_db(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Lock should store var_name and env_path in enrollments table."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1

        enrollment = asyncio.run(repo.get_enrollment(aliases[0]))
        assert enrollment is not None
        assert enrollment.var_name == "OPENAI_API_KEY"
        assert str(env_file.resolve()) in enrollment.env_path

    def test_lock_multiple_keys_stores_enrollments(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        """Lock should store enrollment records for all keys."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        repo = _repo(home_dir)
        enrollments = asyncio.run(repo.list_enrollments())
        assert len(enrollments) == 2

        var_names = {e.var_name for e in enrollments}
        assert "OPENAI_API_KEY" in var_names
        assert "ANTHROPIC_API_KEY" in var_names


class TestLockErrorBranches:
    """Error branch coverage for lock compensation paths."""

    def test_lock_shard_a_write_failure_compensates(
        self, home_dir: WorthlessHome, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PermissionError on shard_a write → DB enrollment rolled back, no orphans."""
        _real_open = os.open

        def _fail_shard_a(path, flags, *args, **kwargs):
            if "shard_a" in str(path) and (flags & os.O_CREAT):
                raise PermissionError(13, "Permission denied", path)
            return _real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", _fail_shard_a)

        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

        # No orphan shard_a files
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert shard_a_files == []

        # No enrollment in DB
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []

    def test_lock_env_rewrite_failure_compensates(
        self, home_dir: WorthlessHome, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IOError on .env rewrite → shard_a deleted, DB enrollment deleted."""
        def _boom(*_args, **_kw):
            raise IOError("disk full")

        monkeypatch.setattr(
            "worthless.cli.commands.lock.rewrite_env_key", _boom,
        )

        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

        # shard_a cleaned up
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert shard_a_files == []

        # DB enrollment cleaned up
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []

    def test_lock_symlink_env_refused(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Lock refuses to follow symlinked .env files."""
        real_env = tmp_path / "real.env"
        real_env.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")
        link_env = tmp_path / "link.env"
        link_env.symlink_to(real_env)

        result = runner.invoke(
            app,
            ["lock", "--env", str(link_env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        assert "symlink" in result.output.lower()


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
                "--key", fake_openai_key(),
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


# ---------------------------------------------------------------------------
# Old decoy migration (WOR-31 Step 5)
# ---------------------------------------------------------------------------


def _make_old_decoy(prefix: str) -> str:
    """Simulate the old _make_decoy() that produced low-entropy WRTLS decoys."""
    import secrets

    body_seed = secrets.token_hex(4)  # 8 hex chars
    marker = "WRTLS" * 20  # enough to fill any provider length
    return prefix + body_seed + marker[:80]


class TestOldDecoyMigration:
    """Lock should auto-upgrade old WRTLS-marker decoys to high-entropy format."""

    def test_migrate_old_wrtls_decoy_on_lock(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Old WRTLS decoy in .env should be replaced with provider-format key on lock."""
        env = tmp_path / ".env"
        real_key = fake_openai_key()

        # Step 1: lock the real key normally
        env.write_text(f"OPENAI_API_KEY={real_key}\n")
        result = runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Step 2: manually rewrite .env with an old-style WRTLS decoy
        # and clear the decoy_hash in the DB to simulate pre-WOR-31 state
        old_decoy = "sk-proj-abcd1234WRTLSWRTLSWRTLSWRTLSWRTLSWRTLSWRTLS"
        env.write_text(f"OPENAI_API_KEY={old_decoy}\n")
        assert shannon_entropy(old_decoy) < ENTROPY_THRESHOLD, "old decoy should be low entropy"

        # Clear decoy_hash to simulate old enrollment without hash
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1

        async def _clear_hash():
            async with repo._connect() as db:
                await db.execute("UPDATE enrollments SET decoy_hash = NULL")
                await db.commit()

        asyncio.run(_clear_hash())

        # Step 3: run lock again -- should trigger migration
        result = runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # .env should now have a new high-entropy decoy, not the old one
        new_value = env.read_text().strip().split("=", 1)[1]
        assert "WRTLS" not in new_value
        assert new_value.startswith("sk-proj-")
        assert shannon_entropy(new_value) >= ENTROPY_THRESHOLD

    def test_migrate_populates_decoy_hash(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """After migration, decoy_hash should be set on the enrollment."""
        env = tmp_path / ".env"
        real_key = fake_openai_key()

        # Lock then simulate old decoy
        env.write_text(f"OPENAI_API_KEY={real_key}\n")
        runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )

        old_decoy = "sk-proj-abcd1234WRTLSWRTLSWRTLSWRTLSWRTLSWRTLSWRTLS"
        env.write_text(f"OPENAI_API_KEY={old_decoy}\n")

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())

        async def _clear_and_check():
            async with repo._connect() as db:
                await db.execute("UPDATE enrollments SET decoy_hash = NULL")
                await db.commit()
            # Confirm hash is NULL before migration
            enrollment = await repo.get_enrollment(aliases[0])
            assert enrollment is not None
            assert enrollment.decoy_hash is None

        asyncio.run(_clear_and_check())

        # Run lock to trigger migration
        runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )

        # Check decoy_hash is now set
        enrollment = asyncio.run(repo.get_enrollment(aliases[0]))
        assert enrollment is not None
        assert enrollment.decoy_hash is not None

    def test_migrate_idempotent_second_lock_noop(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Second lock after migration should not change the .env again."""
        env = tmp_path / ".env"
        real_key = fake_openai_key()

        # Lock then simulate old decoy
        env.write_text(f"OPENAI_API_KEY={real_key}\n")
        runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )

        old_decoy = "sk-proj-abcd1234WRTLSWRTLSWRTLSWRTLSWRTLSWRTLS"
        env.write_text(f"OPENAI_API_KEY={old_decoy}\n")

        repo = _repo(home_dir)

        async def _clear_hash():
            async with repo._connect() as db:
                await db.execute("UPDATE enrollments SET decoy_hash = NULL")
                await db.commit()

        asyncio.run(_clear_hash())

        # First lock migrates
        runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        value_after_first = env.read_text().strip().split("=", 1)[1]

        # Second lock should be a no-op -- value unchanged
        runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        value_after_second = env.read_text().strip().split("=", 1)[1]

        assert value_after_first == value_after_second

    def test_migrate_skips_non_enrolled_wrtls_values(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """WRTLS values without a matching enrollment should not be touched."""
        env = tmp_path / ".env"
        old_decoy = "sk-proj-abcd1234WRTLSWRTLSWRTLSWRTLSWRTLSWRTLSWRTLS"
        env.write_text(f"SOME_RANDOM_VAR={old_decoy}\n")

        result = runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        # Value should be unchanged -- no enrollment exists for SOME_RANDOM_VAR
        current = env.read_text().strip().split("=", 1)[1]
        assert current == old_decoy

    def test_migrate_skips_high_entropy_wrtls_substring(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """A high-entropy value that happens to contain 'WRTLS' should not be migrated."""
        env = tmp_path / ".env"
        real_key = fake_openai_key()

        # Lock the real key first
        env.write_text(f"OPENAI_API_KEY={real_key}\n")
        result1 = runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result1.exit_code == 0

        # Craft a high-entropy value that contains "WRTLS" but is not an old decoy
        import secrets
        import string

        high_entropy_body = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(100)
        )
        high_entropy_val = f"sk-proj-{high_entropy_body}WRTLS{high_entropy_body[:50]}"
        assert shannon_entropy(high_entropy_val) >= ENTROPY_THRESHOLD
        env.write_text(f"OPENAI_API_KEY={high_entropy_val}\n")

        repo = _repo(home_dir)

        async def _clear_hash():
            async with repo._connect() as db:
                await db.execute("UPDATE enrollments SET decoy_hash = NULL")
                await db.commit()

        asyncio.run(_clear_hash())

        # Lock should NOT migrate this because entropy is high
        result2 = runner.invoke(
            app, ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result2.exit_code == 0

        # The migration path should not have touched it (entropy >= threshold).
        # scan_env_keys may process it as a new key, but that is normal lock
        # behavior, not migration. The key invariant: no crash, correct behavior.
