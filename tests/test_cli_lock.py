"""Tests for the lock and enroll CLI commands."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome

from tests.conftest import make_repo as _repo
from tests.helpers import fake_anthropic_key, fake_openai_key

runner = CliRunner()


# ---------------------------------------------------------------------------
# WOR-207 Phase 2: Format-preserving lock + decoy deletion
# ---------------------------------------------------------------------------


class TestLockFormatPreserving:
    """Lock should use split_key_fp, write shard-A to .env, store prefix/charset in DB."""

    def test_lock_writes_shard_a_to_env(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """After lock, .env should contain a format-valid shard-A (not a decoy)."""
        original_key = env_file.read_text().strip().split("=", 1)[1]
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Parse the OPENAI_API_KEY line specifically
        from dotenv import dotenv_values

        parsed = dotenv_values(env_file)
        new_value = parsed["OPENAI_API_KEY"]
        # Shard-A must preserve the prefix
        assert new_value.startswith("sk-proj-")
        # Shard-A must differ from original key (it's a random share)
        assert new_value != original_key
        # Shard-A must have the same length as original key
        assert len(new_value) == len(original_key)

    def test_lock_writes_base_url_to_env(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """After lock, .env should contain OPENAI_BASE_URL pointing to proxy."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        content = env_file.read_text()
        assert "OPENAI_BASE_URL=" in content
        # URL must contain the alias in the path
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1
        alias = aliases[0]
        # Extract the BASE_URL value
        for line in content.splitlines():
            if line.startswith("OPENAI_BASE_URL="):
                url = line.split("=", 1)[1]
                assert alias in url
                assert "/v1" in url
                assert "8787" in url  # default port
                break
        else:
            pytest.fail("OPENAI_BASE_URL not found in .env")

    def test_lock_reads_existing_base_url_from_env(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """If user's .env has OPENROUTER_API_KEY + OPENROUTER_BASE_URL pointing
        at OpenRouter, lock stores that URL in DB and rewrites the var (NAME
        unchanged) to the local proxy URL — not the canonical OPENAI_BASE_URL.

        worthless-8rqs core flow: respect the user's variable names, route
        per-enrollment to the right upstream.
        """
        from worthless.storage.repository import ShardRepository

        env = tmp_path / ".env"
        env.write_text(
            f"OPENROUTER_API_KEY={fake_openai_key()}\n"
            "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n"
        )

        result = runner.invoke(
            app,
            ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # DB row stores the user's base_url (the upstream — OpenRouter).
        async def _check():
            repo = ShardRepository(str(home_dir.db_path), home_dir.fernet_key)
            await repo.initialize()
            aliases = await repo.list_keys()
            assert len(aliases) == 1
            enc = await repo.fetch_encrypted(aliases[0])
            return enc.base_url, aliases[0]

        base_url_in_db, alias = asyncio.run(_check())
        assert base_url_in_db == "https://openrouter.ai/api/v1"

        # .env rewrite preserves the var NAME (OPENROUTER_BASE_URL, not OPENAI_BASE_URL).
        from dotenv import dotenv_values

        parsed = dotenv_values(env)
        assert "OPENROUTER_BASE_URL" in parsed
        assert "OPENAI_BASE_URL" not in parsed, (
            "lock rewrote to canonical OPENAI_BASE_URL — should preserve OPENROUTER_BASE_URL"
        )
        assert parsed["OPENROUTER_BASE_URL"].startswith("http://127.0.0.1:")
        assert alias in parsed["OPENROUTER_BASE_URL"]

    def test_lock_refuses_unregistered_attacker_base_url(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """M3 (Blocker #1): if the user's .env has a *_BASE_URL pointing at a
        URL not in the provider registry, lock must refuse rather than store
        the attacker-controlled URL in the DB.

        Threat: attacker tampers with .env, sets
        OPENROUTER_BASE_URL=https://attacker.example/v1. Pre-fix, lock pulled
        that URL into the DB unchanged; the proxy then forwarded the
        reconstructed shard-A as Bearer to attacker.example — exfiltration.

        worthless-rzi1 (P1 follow-up) adds per-request re-validation against
        the registry to close the post-lock-tamper variant of the same attack.
        worthless-8fbg adds RFC1918/loopback hardening on the URL shape itself.
        M3 here is the lock-time minimum: refuse unknown URLs with a hint to
        worthless providers register.
        """
        env = tmp_path / ".env"
        env.write_text(
            f"OPENROUTER_API_KEY={fake_openai_key()}\n"
            "OPENROUTER_BASE_URL=https://attacker.example/v1\n"
        )

        result = runner.invoke(
            app,
            ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )

        assert result.exit_code != 0, (
            f"lock should refuse unknown upstream URL, but exit_code={result.exit_code}; "
            f"output={result.output[:300]}"
        )
        # Error message should name the offending URL and point at the fix.
        out = result.output.lower()
        assert "attacker.example" in out, (
            f"error message should name the rejected URL; got: {result.output[:400]}"
        )
        assert "providers register" in out or "register" in out, (
            "error message should hint at 'worthless providers register'; "
            f"got: {result.output[:400]}"
        )

        # DB must not have stored the attacker URL — no enrollment created.
        async def _check_no_enrollment():
            from worthless.storage.repository import ShardRepository

            repo = ShardRepository(str(home_dir.db_path), home_dir.fernet_key)
            await repo.initialize()
            aliases = await repo.list_keys()
            return aliases

        aliases = asyncio.run(_check_no_enrollment())
        assert aliases == [], f"DB should have no enrollments after refused lock, found: {aliases}"

    def test_lock_keys_only_skips_base_url(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """--keys-only flag should skip BASE_URL writing."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file), "--keys-only"],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        content = env_file.read_text()
        assert "BASE_URL" not in content

    def test_lock_stores_prefix_charset_in_db(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """After lock, DB shards row should have prefix and charset set."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # Check DB directly for prefix/charset
        conn = sqlite3.connect(str(home_dir.db_path))
        try:
            row = conn.execute("SELECT prefix, charset FROM shards LIMIT 1").fetchone()
        finally:
            conn.close()
        assert row is not None
        prefix, charset = row
        assert prefix == "sk-proj-"
        assert charset is not None
        assert len(charset) > 0

    def test_lock_writes_no_shard_a_files(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """After lock, shard_a_dir should have ZERO files (SR-09: no file fallback)."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert len(shard_a_files) == 0

    def test_relock_skips_enrolled_via_db(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """Second lock should skip keys that already have an enrollment in DB."""
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        result1 = runner.invoke(app, ["lock", "--env", str(env_file)], env=env_vars)
        assert result1.exit_code == 0

        value_after_first = env_file.read_text().strip().split("\n")[0].split("=", 1)[1]

        result2 = runner.invoke(app, ["lock", "--env", str(env_file)], env=env_vars)
        assert result2.exit_code == 0

        value_after_second = env_file.read_text().strip().split("\n")[0].split("=", 1)[1]
        # Value should NOT change on re-lock (key already enrolled)
        assert value_after_first == value_after_second

    def test_lock_base_url_contains_alias(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """BASE_URL path must include the key alias for proxy routing."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        alias = aliases[0]

        content = env_file.read_text()
        for line in content.splitlines():
            if "BASE_URL" in line:
                assert f"/{alias}/v1" in line
                break


class TestDotenvAddOrRewrite:
    """Tests for the add_or_rewrite_env_key helper."""

    def test_add_or_rewrite_creates_new_var(self, tmp_path: Path) -> None:
        """add_or_rewrite should append a new variable if it doesn't exist."""
        from worthless.cli.dotenv_rewriter import add_or_rewrite_env_key

        env = tmp_path / ".env"
        env.write_text("EXISTING=value\n")

        add_or_rewrite_env_key(env, "NEW_VAR", "new_value")

        content = env.read_text()
        assert "EXISTING=value" in content
        assert "NEW_VAR=new_value" in content

    def test_add_or_rewrite_updates_existing(self, tmp_path: Path) -> None:
        """add_or_rewrite should update an existing variable in place."""
        from worthless.cli.dotenv_rewriter import add_or_rewrite_env_key

        env = tmp_path / ".env"
        env.write_text("MY_VAR=old_value\nOTHER=keep\n")

        add_or_rewrite_env_key(env, "MY_VAR", "new_value")

        content = env.read_text()
        assert "new_value" in content
        assert "old_value" not in content
        assert "OTHER=keep" in content


class TestScanEnvKeysNoDecoy:
    """scan_env_keys should work without is_decoy parameter after decoy removal."""

    def test_scan_env_keys_no_decoy_param(self, tmp_path: Path) -> None:
        """scan_env_keys should not accept is_decoy parameter."""
        from worthless.cli.dotenv_rewriter import scan_env_keys

        env = tmp_path / ".env"
        env.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")

        # Should work without is_decoy
        keys = scan_env_keys(env)
        assert len(keys) == 1
        assert keys[0][0] == "OPENAI_API_KEY"


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
        """Lock should split key (FP), store shard_b in DB, rewrite .env."""
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env=env_vars,
        )
        assert result.exit_code == 0, result.output

        # .env should be rewritten (different from original)
        from dotenv import dotenv_values

        parsed = dotenv_values(env_file)
        new_value = parsed["OPENAI_API_KEY"]
        assert fake_openai_key()[:24] not in new_value
        # Shard-A should still start with sk-proj-
        assert new_value.startswith("sk-proj-")

        # No shard_a files on disk (SR-09)
        shard_a_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert len(shard_a_files) == 0

        # shard_b should be in DB
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1

    def test_lock_no_env_file_exits_error(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
        """Lock with nonexistent .env should exit with error code."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(tmp_path / "nonexistent.env")],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

    def test_lock_no_api_keys_exits_zero(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
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

    def test_lock_idempotent_skips_enrolled(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """Running lock twice should skip already-enrolled keys."""
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        result1 = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env=env_vars,
        )
        assert result1.exit_code == 0

        # Second run -- should skip the already-enrolled key
        result2 = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env=env_vars,
        )
        assert result2.exit_code == 0
        # Still only one alias in DB
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1

    def test_lock_prefix_preservation(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """Shard-A value should preserve prefix and match original key length."""
        original_key = env_file.read_text().strip().split("=", 1)[1]
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env=env_vars,
        )
        assert result.exit_code == 0

        from dotenv import dotenv_values

        parsed = dotenv_values(env_file)
        shard_a = parsed["OPENAI_API_KEY"]
        assert shard_a.startswith("sk-proj-")
        # Format-preserving: shard-A has same length as original
        assert len(shard_a) == len(original_key)

    def test_lock_multiple_keys(self, home_dir: WorthlessHome, multi_env_file: Path) -> None:
        """Lock should process all API keys in .env."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0

        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 2

    def test_relock_same_key_new_env_rewrites_to_shard_a(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Locking the same real key in a second .env must rewrite the new .env.

        Regression for the silent-no-op bug where the re-lock guard added a DB
        enrollment, printed "1 key(s) protected.", but left the real key in the
        second .env — silently leaking secret material into a file the user
        believed was protected.
        """
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        key = fake_openai_key()

        env_a = tmp_path / "a" / ".env"
        env_b = tmp_path / "b" / ".env"
        env_a.parent.mkdir()
        env_b.parent.mkdir()
        env_a.write_text(f"OPENAI_API_KEY={key}\n")
        env_b.write_text(f"OPENAI_API_KEY={key}\n")

        r1 = runner.invoke(app, ["lock", "--env", str(env_a)], env=env_vars)
        assert r1.exit_code == 0, r1.output

        from dotenv import dotenv_values

        shard_a_first = dotenv_values(env_a)["OPENAI_API_KEY"]
        assert shard_a_first != key, "first lock should already rewrite env_a"

        r2 = runner.invoke(app, ["lock", "--env", str(env_b)], env=env_vars)
        assert r2.exit_code == 0, r2.output

        shard_a_second = dotenv_values(env_b)["OPENAI_API_KEY"]
        assert shard_a_second != key, (
            "re-lock left the real key in the .env — silent secret leak. "
            f"env_b still contains: {shard_a_second[:12]}..."
        )
        # shard-A must still be format-preserving (prefix + length match).
        assert shard_a_second.startswith("sk-proj-")
        assert len(shard_a_second) == len(key)

    def test_relock_same_key_new_env_reconstructs_correctly(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """Shard-A written on re-lock must reconstruct the real key via shard-B.

        Derivation correctness check: the re-lock path must produce a shard-A
        that, combined with the already-stored shard-B, yields the original
        real key. Without this, the proxy could not rebuild the real key.
        """
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        key = fake_openai_key()

        env_a = tmp_path / "a" / ".env"
        env_b = tmp_path / "b" / ".env"
        env_a.parent.mkdir()
        env_b.parent.mkdir()
        env_a.write_text(f"OPENAI_API_KEY={key}\n")
        env_b.write_text(f"OPENAI_API_KEY={key}\n")

        assert runner.invoke(app, ["lock", "--env", str(env_a)], env=env_vars).exit_code == 0
        assert runner.invoke(app, ["lock", "--env", str(env_b)], env=env_vars).exit_code == 0

        from dotenv import dotenv_values

        from worthless.crypto.splitter import reconstruct_key_fp

        shard_a_str = dotenv_values(env_b)["OPENAI_API_KEY"]
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1
        encrypted = asyncio.run(repo.fetch_encrypted(aliases[0]))
        assert encrypted is not None
        stored = repo.decrypt_shard(encrypted)

        reconstructed = reconstruct_key_fp(
            bytearray(shard_a_str.encode()),
            stored.shard_b,
            stored.commitment,
            stored.nonce,
            encrypted.prefix or "",
            encrypted.charset or "",
        )
        assert reconstructed.decode() == key

    def test_relock_compensation_restores_env_on_base_url_failure(
        self, home_dir: WorthlessHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-lock path must restore real key to .env if BASE_URL write fails.

        Without compensation this recreates WOR-280: the re-lock overwrote .env
        with shard-A, then the BASE_URL write crashed, and the real key was
        gone from the file (destructive) while the user saw an error (not a
        silent leak, but unrecoverable partial state).
        """
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        key = fake_openai_key()

        env_a = tmp_path / "a" / ".env"
        env_b = tmp_path / "b" / ".env"
        env_a.parent.mkdir()
        env_b.parent.mkdir()
        env_a.write_text(f"OPENAI_API_KEY={key}\n")
        env_b.write_text(f"OPENAI_API_KEY={key}\n")

        # Prime: lock env_a cleanly so alias is in DB.
        r1 = runner.invoke(app, ["lock", "--env", str(env_a)], env=env_vars)
        assert r1.exit_code == 0, r1.output

        # Now sabotage BASE_URL writes so the re-lock fails mid-way.
        from worthless.cli.commands import lock as lock_mod

        def _boom_on_base_url(*args, **kwargs):
            raise OSError("disk full on BASE_URL write")

        monkeypatch.setattr(lock_mod, "rewrite_env_keys", _boom_on_base_url)

        r2 = runner.invoke(app, ["lock", "--env", str(env_b)], env=env_vars)
        assert r2.exit_code == 1  # expected failure

        from dotenv import dotenv_values

        parsed = dotenv_values(env_b)
        assert parsed["OPENAI_API_KEY"] == key, (
            "Re-lock failed mid-way and left env_b in a broken state — "
            f"expected real key restored, got: {parsed['OPENAI_API_KEY'][:12]}..."
        )

    def test_relock_rolls_back_base_url_when_enrollment_fails(
        self, home_dir: WorthlessHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-lock must restore the *whole* .env if enrollment fails after BASE_URL.

        Step 2 (BASE_URL write) succeeds, step 3 (DB enrollment) fails. Partial-
        restore that only rewrites OPENAI_API_KEY would leave a real key
        coexisting with a proxy BASE_URL — inconsistent state the user can't
        reason about. Whole-file snapshot rollback is the correct fix.
        """
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        key = fake_openai_key()

        env_a = tmp_path / "a" / ".env"
        env_b = tmp_path / "b" / ".env"
        env_a.parent.mkdir()
        env_b.parent.mkdir()
        env_a.write_text(f"OPENAI_API_KEY={key}\n")
        env_b.write_text(f"OPENAI_API_KEY={key}\n")

        # Prime: lock env_a cleanly.
        r1 = runner.invoke(app, ["lock", "--env", str(env_a)], env=env_vars)
        assert r1.exit_code == 0, r1.output

        # Capture env_b pre-content (exactly the snapshot we expect restored).
        original_content = env_b.read_text()

        # Sabotage enrollment (step 3) so step 1 (key) and step 2 (BASE_URL)
        # are already on disk when the failure fires.
        from worthless.storage import repository as repo_mod

        async def _boom_on_enrollment(self, *args, **kwargs):
            raise RuntimeError("db write failed after BASE_URL was persisted")

        monkeypatch.setattr(repo_mod.ShardRepository, "add_enrollment", _boom_on_enrollment)

        r2 = runner.invoke(app, ["lock", "--env", str(env_b)], env=env_vars)
        assert r2.exit_code == 1, r2.output

        # Whole-file restoration: env_b must be byte-identical to pre-run.
        # Partial compensation would leave OPENAI_BASE_URL lingering.
        restored = env_b.read_text()
        assert restored == original_content, (
            "env_b should be restored to its pre-re-lock snapshot — "
            "partial compensation would leak BASE_URL changes."
        )
        assert "OPENAI_BASE_URL" not in restored, (
            "BASE_URL must not persist when re-lock fails mid-way."
        )

    def test_lock_acquires_and_releases_lock_file(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Lock file should not exist after command completes."""
        env_vars = {"WORTHLESS_HOME": str(home_dir.base_dir)}
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env=env_vars,
        )
        assert result.exit_code == 0
        assert not home_dir.lock_file.exists()

    def test_lock_with_provider_override(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
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


class TestLockDBAndFiles:
    """Lock stores enrollment in SQLite (no shard_a files per SR-09)."""

    def test_lock_creates_no_shard_a_files(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """After lock, shard_a_dir should have ZERO files (SR-09)."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        all_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert len(all_files) == 0

    def test_lock_multiple_keys_no_shard_a_files(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        """After locking multiple keys, still zero shard_a files (SR-09)."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(multi_env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        all_files = [f for f in home_dir.shard_a_dir.iterdir() if f.is_file()]
        assert len(all_files) == 0

    def test_lock_stores_enrollment_in_db(self, home_dir: WorthlessHome, env_file: Path) -> None:
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

    def test_lock_env_rewrite_failure_compensates(
        self, home_dir: WorthlessHome, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """IOError on .env rewrite -> DB enrollment deleted, .env unchanged."""
        original_content = env_file.read_text()

        def _boom(*_args, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(
            "worthless.cli.commands.lock.rewrite_env_keys",
            _boom,
        )

        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

        # DB enrollment cleaned up
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []

        # .env unchanged after failed operation
        assert env_file.read_text() == original_content

    def test_lock_symlink_env_refused(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
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

    def test_lock_db_write_failure_exits_clean(
        self, home_dir: WorthlessHome, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sqlite3.DatabaseError during store_enrolled -> exit_code=1 with WRTLS."""

        async def _boom(self, *args, **kwargs):
            raise sqlite3.DatabaseError("disk I/O error")

        monkeypatch.setattr(
            "worthless.storage.repository.ShardRepository.store_enrolled",
            _boom,
        )

        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1
        assert "WRTLS" in result.output

    def test_lock_scan_env_keys_oserror_exits_clean(
        self, home_dir: WorthlessHome, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError in scan_env_keys -> exit_code=1."""

        def _boom(path):
            raise OSError("permission denied")

        monkeypatch.setattr(
            "worthless.cli.commands.lock.scan_env_keys",
            _boom,
        )

        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1


class TestLockBaseUrlFailureRestoresEnv:
    """CR-1: If BASE_URL write fails after key rewrite, original key must be restored."""

    def test_base_url_failure_restores_original_key(
        self, home_dir: WorthlessHome, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError on add_or_rewrite_env_key -> .env restored to original key."""
        original_content = env_file.read_text()
        original_key = original_content.strip().split("=", 1)[1]

        _call_count = 0

        def _boom_on_base_url(*args, **kwargs):
            nonlocal _call_count
            _call_count += 1
            # Let rewrite_env_key succeed (key replacement), but fail on
            # add_or_rewrite_env_key (BASE_URL write)
            raise OSError("disk full on BASE_URL write")

        monkeypatch.setattr(
            "worthless.cli.commands.lock.rewrite_env_keys",
            _boom_on_base_url,
        )

        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 1

        # The original key MUST be restored — not left as shard-A with no DB
        from dotenv import dotenv_values

        parsed = dotenv_values(env_file)
        assert parsed["OPENAI_API_KEY"] == original_key, (
            "Original key not restored after BASE_URL write failure"
        )

        # DB should be clean
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == [], "DB shard not cleaned up after failure"


class TestProxyBaseUrl:
    """Unit tests for _proxy_base_url helper."""

    def test_proxy_base_url_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from worthless.cli.commands.lock import _proxy_base_url

        monkeypatch.delenv("WORTHLESS_PORT", raising=False)
        url = _proxy_base_url("my-alias")
        assert url == "http://127.0.0.1:8787/my-alias/v1"

    def test_proxy_base_url_custom_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from worthless.cli.commands.lock import _proxy_base_url

        monkeypatch.setenv("WORTHLESS_PORT", "9999")
        url = _proxy_base_url("test-key")
        assert url == "http://127.0.0.1:9999/test-key/v1"


class TestLockChmodEnvFile:
    """Lock tightens .env permissions after writing shard-A."""

    def test_lock_restricts_env_permissions(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
        """After lock, .env should have no group/other perms."""
        import stat

        env = tmp_path / ".env"
        env.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")
        env.chmod(0o644)  # world-readable initially

        result = runner.invoke(
            app,
            ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        mode = env.stat().st_mode
        assert not (mode & stat.S_IRWXG), "Group permissions should be removed"
        assert not (mode & stat.S_IRWXO), "Other permissions should be removed"

    def test_lock_keeps_perms_if_already_strict(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        """If .env is already 0o600, lock should not error."""
        env = tmp_path / ".env"
        env.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")
        env.chmod(0o600)

        result = runner.invoke(
            app,
            ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output


class TestLockNextStepHint:
    """Tests for post-lock next-step guidance (WOR-178)."""

    def test_lock_prints_next_step_hint(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """After successful lock, output should contain a 'Next:' hint."""
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output
        assert "Next:" in result.output

    def test_lock_hint_suppressed_in_json_mode(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """In --json mode, the 'Next:' hint should not appear."""
        result = runner.invoke(
            app,
            ["--json", "lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output
        assert "Next:" not in result.output

    def test_lock_hint_suppressed_in_quiet_mode(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """In --quiet mode, the 'Next:' hint should not appear."""
        result = runner.invoke(
            app,
            ["--quiet", "lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output
        assert "Next:" not in result.output

    def test_lock_no_hint_when_no_keys_found(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
        """When no keys are found, the hint should not appear."""
        env = tmp_path / ".env"
        env.write_text("DATABASE_URL=postgres://localhost/db\n")
        result = runner.invoke(
            app,
            ["lock", "--env", str(env)],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0
        assert "Next:" not in result.output


class TestPrintHint:
    """Unit tests for WorthlessConsole.print_hint (WOR-178)."""

    def test_print_hint_normal_mode(self, capsys: pytest.CaptureFixture) -> None:
        """print_hint should output the message in normal mode."""
        from worthless.cli.console import WorthlessConsole

        c = WorthlessConsole(quiet=False, json_mode=False)
        c.print_hint("Next: do something")
        captured = capsys.readouterr()
        assert "Next: do something" in captured.err

    def test_print_hint_suppressed_quiet(self, capsys: pytest.CaptureFixture) -> None:
        """print_hint should be suppressed in quiet mode."""
        from worthless.cli.console import WorthlessConsole

        c = WorthlessConsole(quiet=True, json_mode=False)
        c.print_hint("Next: do something")
        captured = capsys.readouterr()
        assert "Next:" not in captured.err
        assert "Next:" not in captured.out

    def test_print_hint_suppressed_json(self, capsys: pytest.CaptureFixture) -> None:
        """print_hint should be suppressed in json_mode."""
        from worthless.cli.console import WorthlessConsole

        c = WorthlessConsole(quiet=False, json_mode=True)
        c.print_hint("Next: do something")
        captured = capsys.readouterr()
        assert "Next:" not in captured.err
        assert "Next:" not in captured.out


class TestEnrollCommand:
    """Tests for `worthless enroll`."""

    def test_enroll_explicit_args(self, home_dir: WorthlessHome) -> None:
        """Enroll with explicit alias, key, and provider."""
        result = runner.invoke(
            app,
            [
                "enroll",
                "--alias",
                "my-test-key",
                "--key",
                fake_openai_key(),
                "--provider",
                "openai",
            ],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result.exit_code == 0, result.output

        # shard_b should be in DB with prefix/charset
        repo = _repo(home_dir)
        stored = asyncio.run(repo.retrieve("my-test-key"))
        assert stored is not None
        assert stored.provider == "openai"

    def test_enroll_duplicate_alias_errors_without_destroying_first(
        self, home_dir: WorthlessHome
    ) -> None:
        """Re-enrolling the same alias must error cleanly without deleting
        the first enrollment's data."""
        # First enrollment — should succeed
        result1 = runner.invoke(
            app,
            [
                "enroll",
                "--alias",
                "dup-test",
                "--key",
                fake_openai_key(),
                "--provider",
                "openai",
            ],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result1.exit_code == 0, result1.output

        # Verify first enrollment is intact
        repo = _repo(home_dir)
        stored_before = asyncio.run(repo.retrieve("dup-test"))
        assert stored_before is not None

        # Second enrollment — same alias — should fail
        result2 = runner.invoke(
            app,
            [
                "enroll",
                "--alias",
                "dup-test",
                "--key",
                fake_openai_key(),
                "--provider",
                "openai",
            ],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        assert result2.exit_code != 0, (
            "Re-enrolling the same alias should fail, but exit_code was 0"
        )

        # Original enrollment must still be intact
        stored_after = asyncio.run(repo.retrieve("dup-test"))
        assert stored_after is not None, (
            "First enrollment's shard was destroyed by failed re-enrollment"
        )

    def test_enroll_db_failure_exits_clean(
        self, home_dir: WorthlessHome, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB failure during enroll -> clean exit, no partial state."""

        async def _db_boom(self, *args, **kwargs):
            raise sqlite3.DatabaseError("disk I/O error")

        monkeypatch.setattr(
            "worthless.storage.repository.ShardRepository.store_enrolled",
            _db_boom,
        )

        result = runner.invoke(
            app,
            [
                "enroll",
                "--alias",
                "orphan-test",
                "--key",
                fake_openai_key(),
                "--provider",
                "openai",
            ],
            env={"WORTHLESS_HOME": str(home_dir.base_dir)},
        )
        # Command should fail
        assert result.exit_code != 0

        # No DB shard should remain
        repo = _repo(home_dir)
        aliases = asyncio.run(repo.list_keys())
        assert aliases == []
