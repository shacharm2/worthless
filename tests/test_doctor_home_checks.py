"""Doctor home-mismatch + alias-not-in-DB checks, and lock non-default home warning.

TDD-first: all tests are written before any implementation. They must all fail
(red) before any code is written.

Coverage:
* Lock warns when WORTHLESS_HOME is set (test 1)
* Lock stays silent when using the default home (test 2)
* Lock does NOT warn when the lock itself fails (test 3)
* Doctor detects a running proxy using a different WORTHLESS_HOME (test 4)
* Doctor skips the mismatch check gracefully when no proxy is running (test 5)
* Doctor handles a corrupt / unreadable pid file without crashing (test 6)
* Doctor handles a dead process (read_process_env returns {}) without crashing (test 7)
* Doctor warns when a .env BASE_URL references an alias absent from the DB (test 8)
* Doctor emits two distinct warnings when both orphan AND alias-not-in-DB
  conditions are present (test 9)
* _collect_alias_issues: no false positive for an enrolled alias (unit, test 10)
* _collect_alias_issues: malformed BASE_URL that doesn't match the regex (unit, test 11)
* _collect_alias_issues: permission-denied .env is silently skipped (unit, test 12)
* _collect_alias_issues: two missing aliases in same file → two issues (unit, test 13)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.cli.process import pid_path, write_pid

from tests.helpers import fake_openai_key

runner = CliRunner(mix_stderr=False)


@pytest.fixture
def fake_home(tmp_path: Path) -> WorthlessHome:
    """Bootstrapped tmp WorthlessHome with no enrollments."""
    return ensure_home(tmp_path / ".worthless")


# ---------------------------------------------------------------------------
# Lock non-default home warning
# ---------------------------------------------------------------------------


class TestLockHomeMismatchWarning:
    def test_lock_warns_when_worthless_home_set(self, tmp_path: Path) -> None:
        """When WORTHLESS_HOME is set, lock prints a non-default-home warning."""
        home = ensure_home(tmp_path / ".worthless")
        env_file = tmp_path / ".env"
        env_file.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")
        result = runner.invoke(
            app,
            ["lock", "--env", str(env_file)],
            env={"WORTHLESS_HOME": str(home.base_dir)},
        )
        assert result.exit_code == 0, result.output
        assert "Warning: using non-default home" in result.output
        assert "WORTHLESS_HOME is set" in result.output

    def test_lock_silent_when_default_home(self, tmp_path: Path, monkeypatch) -> None:
        """When WORTHLESS_HOME is NOT set, lock produces no home warning."""
        monkeypatch.delenv("WORTHLESS_HOME", raising=False)
        home = ensure_home(tmp_path / ".worthless")
        monkeypatch.setattr("worthless.cli.commands.lock.get_home", lambda: home)
        env_file = tmp_path / ".env"
        env_file.write_text(f"OPENAI_API_KEY={fake_openai_key()}\n")
        result = runner.invoke(app, ["lock", "--env", str(env_file)])
        assert result.exit_code == 0, result.output
        assert "WORTHLESS_HOME is set" not in result.output

    def test_lock_no_warning_on_failed_lock(self, tmp_path: Path) -> None:
        """When lock fails (missing .env), the non-default-home warning is never printed.

        The warning is gated inside the success branch of _lock_keys. If the lock
        itself errors out, we must never reach that branch — even when WORTHLESS_HOME
        is set — so the user doesn't see a spurious warning alongside a failure message.
        """
        home = ensure_home(tmp_path / ".worthless")
        missing_env = tmp_path / "nonexistent.env"
        result = runner.invoke(
            app,
            ["lock", "--env", str(missing_env)],
            env={"WORTHLESS_HOME": str(home.base_dir)},
        )
        assert result.exit_code != 0
        assert "WORTHLESS_HOME is set" not in result.output


# ---------------------------------------------------------------------------
# Doctor: home mismatch check
# ---------------------------------------------------------------------------


class TestDoctorHomeMismatch:
    def test_doctor_detects_home_mismatch(
        self, tmp_path: Path, fake_home: WorthlessHome, monkeypatch
    ) -> None:
        """Doctor warns when the running proxy's WORTHLESS_HOME differs from the shell's."""
        write_pid(pid_path(fake_home), pid=12345, port=8787)
        other_home = str(tmp_path / "other-home")
        # raising=False: read_process_env doesn't exist before implementation;
        # monkeypatch creates the attribute so the test fails on the assertion,
        # not on the patch setup.
        monkeypatch.setattr(
            "worthless.cli.commands.doctor.read_process_env",
            lambda pid: {"WORTHLESS_HOME": other_home},
        )
        result = runner.invoke(
            app,
            ["doctor"],
            env={"WORTHLESS_HOME": str(fake_home.base_dir)},
        )
        assert "home mismatch" in result.output
        assert "Fix: unset WORTHLESS_HOME" in result.output

    def test_doctor_skips_mismatch_check_when_proxy_not_running(
        self, fake_home: WorthlessHome
    ) -> None:
        """When no proxy pid file exists, doctor skips the mismatch check silently."""
        # No write_pid call → pid file absent
        result = runner.invoke(
            app,
            ["doctor"],
            env={"WORTHLESS_HOME": str(fake_home.base_dir)},
        )
        assert "home mismatch" not in result.output
        assert result.exit_code == 0

    def test_doctor_handles_corrupt_pid_file(
        self, tmp_path: Path, fake_home: WorthlessHome
    ) -> None:
        """A corrupt pid file (not the expected format) does not crash doctor.

        read_pid is expected to return None on parse failure; _check_home_mismatch
        then returns False early. This test proves that invariant survives a real
        bad file on disk — not just an absent one.
        """
        pid_path(fake_home).write_text("not-a-valid-pid-record\n", encoding="utf-8")
        result = runner.invoke(
            app,
            ["doctor"],
            env={"WORTHLESS_HOME": str(fake_home.base_dir)},
        )
        assert "home mismatch" not in result.output
        assert result.exit_code == 0

    def test_doctor_handles_dead_process_returns_empty_env(
        self, tmp_path: Path, fake_home: WorthlessHome, monkeypatch
    ) -> None:
        """If the process dies after the PID read (TOCTOU), read_process_env returns {}.

        When environ() returns {} the function falls back to treating the proxy
        as using the default home. Doctor must not crash and must not emit a
        spurious traceback — only possibly a mismatch warning, never an exception.
        """
        write_pid(pid_path(fake_home), pid=99999, port=8787)
        # Simulate psutil catching NoSuchProcess and returning empty dict
        monkeypatch.setattr(
            "worthless.cli.commands.doctor.read_process_env",
            lambda pid: {},
        )
        result = runner.invoke(
            app,
            ["doctor"],
            env={"WORTHLESS_HOME": str(fake_home.base_dir)},
        )
        # Must not crash with an unhandled exception
        assert result.exit_code in (0, 1)  # warn=1 is OK; traceback is not
        assert "Traceback" not in (result.output + (result.stderr or ""))


# ---------------------------------------------------------------------------
# Doctor: alias-not-in-DB check
# ---------------------------------------------------------------------------


class TestDoctorAliasNotInDb:
    def test_doctor_detects_alias_not_in_db(
        self, tmp_path: Path, fake_home: WorthlessHome, monkeypatch
    ) -> None:
        """Doctor warns when a .env BASE_URL alias has no shard in the current DB."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "OPENAI_API_KEY=sk-proj-shard-a-placeholder\n"
            "OPENAI_BASE_URL=http://127.0.0.1:8787/openai-abc12345/v1\n"
        )
        result = runner.invoke(
            app,
            ["doctor"],
            env={"WORTHLESS_HOME": str(fake_home.base_dir)},
        )
        assert "openai-abc12345" in result.output
        assert "no shard" in result.output

    def test_doctor_alias_check_separate_from_orphan_check(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Both orphan AND alias-not-in-DB conditions emit two distinct warnings."""
        from worthless.storage.repository import ShardRepository, StoredShard
        from worthless.crypto.splitter import split_key_fp

        home = ensure_home(tmp_path / ".worthless")
        deleted_env = tmp_path / "deleted.env"  # never created → orphan

        repo = ShardRepository(str(home.db_path), home.fernet_key)

        async def _setup() -> None:
            await repo.initialize()
            key = fake_openai_key()
            sr = split_key_fp(key, "sk-proj-", "openai")
            stored = StoredShard(
                shard_b=sr.shard_b,
                commitment=sr.commitment,
                nonce=sr.nonce,
                provider="openai",
            )
            await repo.store_enrolled(
                "openai-orphan111",
                stored,
                var_name="OPENAI_API_KEY",
                env_path=str(deleted_env),
                prefix=sr.prefix,
                charset=sr.charset,
                base_url=None,
            )
            sr.zero()

        asyncio.run(_setup())

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "OPENAI_BASE_URL=http://127.0.0.1:8787/openai-phantom999/v1\n"
        )

        result = runner.invoke(
            app,
            ["doctor"],
            env={"WORTHLESS_HOME": str(home.base_dir)},
        )

        from worthless.cli.orphans import PROBLEM_PHRASE

        # Orphan warning (detected by existing code)
        assert PROBLEM_PHRASE in result.output
        # Alias-not-in-DB is a SEPARATE warning with a distinct phrase
        # (only present once _check_alias_not_in_db is wired in)
        assert "phantom999" in result.output


# ---------------------------------------------------------------------------
# _collect_alias_issues — unit tests (call the helper directly)
# ---------------------------------------------------------------------------


class TestCollectAliasIssuesUnit:
    """Direct unit tests for _collect_alias_issues.

    These exercise the helper in isolation so the integration tests above
    can stay focused on CLI wiring without duplicating every edge case.
    """

    def test_no_false_positive_for_enrolled_alias(self, tmp_path: Path) -> None:
        """An alias that IS in known_aliases is never reported as missing."""
        from worthless.cli.commands.doctor import _collect_alias_issues

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENAI_BASE_URL=http://127.0.0.1:8787/my-enrolled-alias/v1\n",
            encoding="utf-8",
        )
        issues = _collect_alias_issues(
            {env_file},
            {"my-enrolled-alias"},
            "worthless.db",
        )
        assert issues == []

    def test_malformed_url_no_match_no_crash(self, tmp_path: Path) -> None:
        """A BASE_URL that doesn't match the alias regex produces no issue and no crash.

        Example: double-slash path ``http://127.0.0.1:8787//v1`` has no alias segment.
        """
        from worthless.cli.commands.doctor import _collect_alias_issues

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENAI_BASE_URL=http://127.0.0.1:8787//v1\n",
            encoding="utf-8",
        )
        issues = _collect_alias_issues({env_file}, set(), "worthless.db")
        assert issues == []

    @pytest.mark.skipif(os.getuid() == 0, reason="root bypasses file permissions")
    def test_permission_denied_env_file_silently_skipped(self, tmp_path: Path) -> None:
        """A .env that cannot be read (OSError) is skipped; no crash, no issue emitted."""
        from worthless.cli.commands.doctor import _collect_alias_issues

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENAI_BASE_URL=http://127.0.0.1:8787/secret-alias/v1\n",
            encoding="utf-8",
        )
        env_file.chmod(0o000)
        try:
            issues = _collect_alias_issues({env_file}, set(), "worthless.db")
            assert issues == []
        finally:
            env_file.chmod(0o644)  # restore so tmp_path cleanup can delete the file

    def test_two_missing_aliases_in_same_file_both_reported(self, tmp_path: Path) -> None:
        """Two different missing aliases in one .env produce two separate issue strings."""
        from worthless.cli.commands.doctor import _collect_alias_issues

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENAI_BASE_URL=http://127.0.0.1:8787/missing-openai/v1\n"
            "ANTHROPIC_BASE_URL=http://127.0.0.1:8787/missing-anthropic/v1\n",
            encoding="utf-8",
        )
        issues = _collect_alias_issues({env_file}, set(), "worthless.db")
        assert len(issues) == 2
        aliases_reported = {i.split("'")[1] for i in issues}
        assert aliases_reported == {"missing-openai", "missing-anthropic"}
