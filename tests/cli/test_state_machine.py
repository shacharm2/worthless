"""Cross-command state-machine integration tests (HF8 / worthless-5koc).

The state-machine surface across lock / unlock / scan / status spans DB rows
in ``~/.worthless/db.sqlite`` and matching variables in the user's ``.env``.
Each reachable inconsistency listed below was discovered during the
2026-04-30 dogfood of v0.3.2 and had no test coverage:

  1. DB has shard, .env missing var (orphan — the bug that bit the user)
  2. DB empty, .env has shard-A-shape value (lock crashed pre-commit)
  3. Two .env paths share a var name, one locked, one not (multi-project)
  4. Repeated lock without intervening unlock (idempotent vs clobber)
  5. Manual key rotation in .env between lock and unlock (commitment mismatch)
  6. .env file deleted between lock and unlock
  7. ~/.worthless/db.sqlite deleted between lock and unlock

These tests are deliberately RED first. Each encodes the contract that a
hotfix in the v0.3.3 sprint (HF1-HF7) must satisfy. The contract is
intentionally lenient on exact wording — it asserts:

  * the command does not silently succeed on a broken state
  * the user gets a recognisable hint, not a Python traceback
  * the underlying DB / .env state is not corrupted further

so individual fixes can choose exit codes and copy without churning tests.
"""

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

_TEST_KEY = fake_openai_key()
_TEST_KEY_2 = fake_anthropic_key()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(args: list[str], home: WorthlessHome) -> object:
    """Run a CLI command with WORTHLESS_HOME pointed at the test home."""
    return runner.invoke(
        app,
        args,
        env={"WORTHLESS_HOME": str(home.base_dir)},
    )


def _lock(env_file: Path, home: WorthlessHome) -> None:
    """Lock the env file. Asserts the lock itself succeeded — failures here
    are pre-conditions, not the state-machine bug under test."""
    result = _invoke(["lock", "--env", str(env_file)], home)
    assert result.exit_code == 0, f"precondition lock failed:\n{result.output}"


def _dotenv_value(env_file: Path, var: str) -> str | None:
    from dotenv import dotenv_values

    return dotenv_values(env_file).get(var)


def _looks_like_traceback(text: str) -> bool:
    """Heuristic: did a raw Python stack trace leak into user output?"""
    return "Traceback (most recent call last):" in text


def _has_actionable_hint(text: str, *keywords: str) -> bool:
    """Case-insensitive: at least one hint keyword present in user-facing text."""
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def _enrollments(home: WorthlessHome) -> list:
    repo = _repo(home)
    asyncio.run(repo.initialize())
    return asyncio.run(repo.list_enrollments())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
    return env


@pytest.fixture()
def multi_env_file(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={_TEST_KEY}\nANTHROPIC_API_KEY={_TEST_KEY_2}\n")
    return env


# ---------------------------------------------------------------------------
# State 1: DB has shard, .env missing var  (the orphan that bit the user)
# ---------------------------------------------------------------------------


class TestOrphanState:
    """DB row exists but the .env line was deleted by the user.

    Contract: every read-side command (unlock/status/scan) must surface this
    inconsistency with an actionable hint, never a silent success or a
    raw traceback. Operator recovery is HF7's job — these tests only assert
    the state machine *notices*.
    """

    def _orphan(self, env_file: Path, home: WorthlessHome) -> None:
        _lock(env_file, home)
        env_file.write_text("")  # user deleted the locked line

    def test_unlock_on_orphan_does_not_silently_succeed(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        self._orphan(env_file, home_dir)
        result = _invoke(["unlock", "--env", str(env_file)], home_dir)

        assert result.exit_code != 0, (
            "unlock on orphan must fail loudly, not silently succeed.\n" + result.output
        )
        assert not _looks_like_traceback(result.output), (
            "unlock leaked a traceback to the user:\n" + result.output
        )
        assert _has_actionable_hint(
            result.output, "OPENAI_API_KEY", "missing", "orphan", "not found"
        ), f"no actionable hint about the missing var:\n{result.output}"

    def test_status_on_orphan_flags_inconsistency(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        self._orphan(env_file, home_dir)
        result = _invoke(["status"], home_dir)

        assert not _looks_like_traceback(result.output)
        assert _has_actionable_hint(
            result.output, "orphan", "missing", "OPENAI_API_KEY", "inconsistent"
        ), f"status did not flag orphan DB row:\n{result.output}"

    def test_scan_on_orphan_flags_inconsistency(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        self._orphan(env_file, home_dir)
        result = _invoke(["scan", str(env_file.parent)], home_dir)

        assert not _looks_like_traceback(result.output)
        assert _has_actionable_hint(
            result.output, "orphan", "missing", "OPENAI_API_KEY", "inconsistent"
        ), f"scan did not flag orphan DB row:\n{result.output}"


# ---------------------------------------------------------------------------
# State 2: DB empty, .env has shard-A-shape value (lock crashed pre-commit)
# ---------------------------------------------------------------------------


class TestShardWithoutDBRow:
    """User has a value that looks like shard-A in .env, but no DB row.

    Could happen if `worthless lock` crashed between writing .env and
    committing the DB row, or if a teammate copied a locked .env without
    the DB. Contract: unlock must not crash with a KeyError; it must tell
    the user the shard is unrecognised.
    """

    def test_unlock_with_no_db_row_fails_gracefully(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        env = tmp_path / ".env"
        # A shard-A-looking value: same prefix, same length, but never enrolled.
        fake_shard = "sk-proj-" + ("a" * (len(_TEST_KEY) - len("sk-proj-")))
        env.write_text(f"OPENAI_API_KEY={fake_shard}\n")

        result = _invoke(["unlock", "--env", str(env)], home_dir)

        assert result.exit_code != 0, (
            "unlock with no matching DB row must not silently succeed:\n" + result.output
        )
        assert not _looks_like_traceback(result.output)
        assert _has_actionable_hint(
            result.output, "not enrolled", "no shard", "unknown", "not found", "lock"
        ), f"no hint that the value is unrecognised:\n{result.output}"


# ---------------------------------------------------------------------------
# State 3: Multi-project pollution
# ---------------------------------------------------------------------------


class TestMultiProjectPollution:
    """Two .env files exist with the same var name; one is locked, one is not.

    Contract: status / scan must report each path independently and not
    confuse the unlocked .env with the locked one.
    """

    def test_status_lists_locked_path_only(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
        proj_a = tmp_path / "proj-a"
        proj_b = tmp_path / "proj-b"
        proj_a.mkdir()
        proj_b.mkdir()

        env_a = proj_a / ".env"
        env_b = proj_b / ".env"
        env_a.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        env_b.write_text(f"OPENAI_API_KEY={_TEST_KEY_2}\n")

        _lock(env_a, home_dir)

        # proj-b is untouched: same var name, raw key, no DB row for it.
        assert _dotenv_value(env_b, "OPENAI_API_KEY") == _TEST_KEY_2

        # The DB has exactly one enrollment, scoped to proj-a.
        enrollments = _enrollments(home_dir)
        env_paths = {str(e.env_path) for e in enrollments if hasattr(e, "env_path")}
        assert any(str(env_a) in p for p in env_paths) or any("proj-a" in p for p in env_paths), (
            f"proj-a's enrollment is missing from DB: {env_paths}"
        )
        assert not any("proj-b" in p for p in env_paths), (
            f"proj-b leaked into DB despite never being locked: {env_paths}"
        )


# ---------------------------------------------------------------------------
# State 4: Repeated lock without intervening unlock
# ---------------------------------------------------------------------------


class TestRepeatedLock:
    """Calling lock twice on the same .env must not silently double-enroll
    or destroy the original key recovery state."""

    def test_double_lock_does_not_double_enroll(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        _lock(env_file, home_dir)
        first_shard = _dotenv_value(env_file, "OPENAI_API_KEY")
        first_enrollments = _enrollments(home_dir)

        # Second lock — current .env value is already shard-A, not the original key.
        result = _invoke(["lock", "--env", str(env_file)], home_dir)

        # Whether second lock errors or no-ops, the invariants must hold:
        assert not _looks_like_traceback(result.output), (
            "double lock leaked a traceback:\n" + result.output
        )

        second_enrollments = _enrollments(home_dir)
        # Either the same enrollment remains (idempotent) or a clean error
        # was raised. What's NOT acceptable is two enrollments with the same
        # var_name+env_path that mask the original.
        assert len(second_enrollments) <= len(first_enrollments) + 0, (
            f"double lock created phantom enrollments: "
            f"{len(first_enrollments)} -> {len(second_enrollments)}"
        )

        # Whatever .env contains now must still be recoverable through
        # the existing enrollment OR be unchanged. It must NOT be a freshly
        # locked shard of the *previous* shard.
        current = _dotenv_value(env_file, "OPENAI_API_KEY")
        assert current == first_shard, (
            "double lock clobbered shard-A — original key is now unrecoverable.\n"
            f"first:  {first_shard}\nsecond: {current}"
        )


# ---------------------------------------------------------------------------
# State 5: Manual rotation between lock and unlock
# ---------------------------------------------------------------------------


class TestManualRotationMismatch:
    """User edits .env to a different shard-A-shape value after locking
    (e.g. pasted from elsewhere). Commitment in DB no longer matches.

    Contract: unlock must detect the mismatch instead of decrypting to
    garbage and writing it back as the "real" key.
    """

    def test_unlock_detects_commitment_mismatch(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        _lock(env_file, home_dir)
        # Replace shard-A with a different shape-valid string.
        tampered = "sk-proj-" + ("z" * (len(_TEST_KEY) - len("sk-proj-")))
        env_file.write_text(f"OPENAI_API_KEY={tampered}\n")

        result = _invoke(["unlock", "--env", str(env_file)], home_dir)

        assert result.exit_code != 0, (
            "unlock must reject a tampered shard, not silently produce garbage:\n" + result.output
        )
        assert not _looks_like_traceback(result.output)
        assert _has_actionable_hint(
            result.output,
            "commitment",
            "mismatch",
            "tampered",
            "modified",
            "does not match",
            "invalid",
        ), f"no hint about commitment mismatch:\n{result.output}"


# ---------------------------------------------------------------------------
# State 6: .env file deleted between lock and unlock
# ---------------------------------------------------------------------------


class TestEnvFileDeleted:
    def test_unlock_on_missing_env_fails_gracefully(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        _lock(env_file, home_dir)
        env_file.unlink()

        result = _invoke(["unlock", "--env", str(env_file)], home_dir)

        assert result.exit_code != 0
        assert not _looks_like_traceback(result.output), (
            "unlock leaked a traceback when .env was missing:\n" + result.output
        )
        assert _has_actionable_hint(
            result.output, "not found", "no such file", "missing", ".env"
        ), f"no hint about missing .env:\n{result.output}"


# ---------------------------------------------------------------------------
# State 7: db.sqlite deleted between lock and unlock
# ---------------------------------------------------------------------------


class TestDBFileDeleted:
    """If ~/.worthless/db.sqlite is wiped, the shards are unrecoverable.
    Commands must say so plainly instead of crashing on a missing table."""

    def test_unlock_after_db_wipe_fails_gracefully(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        _lock(env_file, home_dir)
        home_dir.db_path.unlink()

        result = _invoke(["unlock", "--env", str(env_file)], home_dir)

        assert result.exit_code != 0
        assert not _looks_like_traceback(result.output), (
            "unlock leaked a traceback after db wipe:\n" + result.output
        )
        assert _has_actionable_hint(
            result.output,
            "enroll",
            "rerun",
            "no such",
            "missing",
            "database",
            "not found",
        ), f"no actionable hint after db wipe:\n{result.output}"

    def test_status_after_db_wipe_does_not_crash(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        _lock(env_file, home_dir)
        home_dir.db_path.unlink()

        result = _invoke(["status"], home_dir)

        # status may legitimately exit 0 (nothing enrolled) or non-zero (broken).
        # What matters: no Python traceback leaks.
        assert not _looks_like_traceback(result.output), (
            "status leaked a traceback after db wipe:\n" + result.output
        )


# ---------------------------------------------------------------------------
# Cross-cutting: multi-key partial unlock
# ---------------------------------------------------------------------------


class TestPartialUnlock:
    """Locking two keys then unlocking one must restore exactly one and
    leave the other locked. Tests the cross-row state machine."""

    def test_partial_unlock_leaves_other_var_locked(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        _lock(multi_env_file, home_dir)
        locked_anthropic = _dotenv_value(multi_env_file, "ANTHROPIC_API_KEY")

        # Unlock only OPENAI_API_KEY.
        result = _invoke(
            ["unlock", "--env", str(multi_env_file), "--var", "OPENAI_API_KEY"],
            home_dir,
        )
        assert not _looks_like_traceback(result.output), result.output

        restored_openai = _dotenv_value(multi_env_file, "OPENAI_API_KEY")
        still_locked = _dotenv_value(multi_env_file, "ANTHROPIC_API_KEY")

        # Per-key unlock must restore the original openai key.
        assert restored_openai == _TEST_KEY, (
            f"OPENAI_API_KEY not restored on partial unlock: {restored_openai!r}"
        )
        # Anthropic must still be the shard-A from the lock step.
        assert still_locked == locked_anthropic, (
            "ANTHROPIC_API_KEY changed despite unlock --var=OPENAI_API_KEY: "
            f"{locked_anthropic!r} -> {still_locked!r}"
        )


# ---------------------------------------------------------------------------
# Cross-cutting: DB schema integrity invariant
# ---------------------------------------------------------------------------


class TestDBSchemaInvariants:
    """After any state-machine transition, the DB must remain queryable
    via plain sqlite3 (no schema corruption, no half-applied migrations)."""

    def test_db_remains_queryable_after_lock(self, home_dir: WorthlessHome, env_file: Path) -> None:
        _lock(env_file, home_dir)
        # Open with stdlib sqlite3 — bypasses our async layer entirely.
        with sqlite3.connect(str(home_dir.db_path)) as con:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert tables, "DB has no tables after lock"
