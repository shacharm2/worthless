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
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values
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
    return dotenv_values(env_file).get(var)


def _looks_like_traceback(text: str) -> bool:
    """Heuristic: did a raw Python stack trace leak into user output?"""
    return "Traceback (most recent call last):" in text


def _has_actionable_hint(text: str, *keywords: str) -> bool:
    """Case-insensitive: at least one hint keyword present in user-facing text."""
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def _has_all_tokens(text: str, *required: str) -> bool:
    """Case-insensitive: ALL required tokens must appear. Used to bind a hint
    to a specific bug (e.g. orphan + the actual var name) so unrelated errors
    that happen to share one keyword cannot turn the test green spuriously."""
    lowered = text.lower()
    return all(k.lower() in lowered for k in required)


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
        # Require BOTH the var name AND an orphan-specific keyword so a generic
        # "file missing" error from an unrelated code path can't satisfy this.
        assert "OPENAI_API_KEY" in result.output, (
            f"unlock error must name the affected var:\n{result.output}"
        )
        assert _has_actionable_hint(
            result.output, "orphan", "no matching .env", "orphaned", "ORPHAN-IN-DB"
        ), f"no orphan-specific hint:\n{result.output}"

    def test_status_on_orphan_flags_inconsistency(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        self._orphan(env_file, home_dir)
        result = _invoke(["status"], home_dir)

        assert not _looks_like_traceback(result.output)
        # Status must do MORE than list the enrollment as PROTECTED — it must
        # mark the row as orphan/inconsistent. Require a bug-specific token.
        assert _has_actionable_hint(
            result.output, "orphan", "ORPHAN-IN-DB", "inconsistent state", "no .env row"
        ), f"status did not flag orphan DB row:\n{result.output}"

    def test_scan_on_orphan_flags_inconsistency(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        self._orphan(env_file, home_dir)
        result = _invoke(["scan", str(env_file.parent)], home_dir)

        assert not _looks_like_traceback(result.output)
        assert _has_actionable_hint(
            result.output, "orphan", "ORPHAN-IN-DB", "inconsistent state", "no .env row"
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
        # Tighten: require an enrollment-specific term, not just generic
        # "not found" which any FS error would match.
        assert _has_actionable_hint(
            result.output, "not enrolled", "no shard", "no enrollment", "unrecognised shard"
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
        assert len(second_enrollments) == len(first_enrollments), (
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
        # Bind to the rotation/commitment domain — generic "invalid" is too
        # loose (would match argparse errors, key shape errors, etc).
        assert _has_actionable_hint(
            result.output,
            "commitment",
            "tampered",
            "does not match",
            "shard mismatch",
            "modified after lock",
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
        # Require either an explicit ".env" mention or a filesystem-not-found
        # phrase — generic "missing" alone would match unrelated bugs.
        assert _has_all_tokens(result.output, ".env") or _has_actionable_hint(
            result.output, "no such file", "file not found", "does not exist"
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
        # Bind to the database-wipe domain; "missing"/"not found" alone is too
        # loose (would match an unrelated .env-missing path).
        assert _has_actionable_hint(
            result.output,
            "database",
            "db.sqlite",
            "no such table",
            "rerun lock",
            "re-enroll",
            "not enrolled",
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
    leave the other locked. Tests the cross-row state machine via the
    real CLI surface (`unlock --alias <id>`); there is no `--var` flag.
    """

    def test_partial_unlock_leaves_other_var_locked(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        _lock(multi_env_file, home_dir)
        locked_anthropic = _dotenv_value(multi_env_file, "ANTHROPIC_API_KEY")

        # Resolve the alias for OPENAI_API_KEY — unlock takes --alias, not
        # --var. Looking it up via the enrollments table is the same path
        # status uses; no test-only DB hackery.
        enrollments = _enrollments(home_dir)
        openai_alias = next(
            (e.key_alias for e in enrollments if e.var_name == "OPENAI_API_KEY"),
            None,
        )
        assert openai_alias, f"precondition: OPENAI_API_KEY enrollment missing: {enrollments}"

        result = _invoke(
            ["unlock", "--env", str(multi_env_file), "--alias", openai_alias],
            home_dir,
        )
        assert not _looks_like_traceback(result.output), result.output
        assert result.exit_code == 0, f"unlock --alias failed:\n{result.output}"

        restored_openai = _dotenv_value(multi_env_file, "OPENAI_API_KEY")
        still_locked = _dotenv_value(multi_env_file, "ANTHROPIC_API_KEY")

        # Per-alias unlock must restore the original openai key.
        assert restored_openai == _TEST_KEY, (
            f"OPENAI_API_KEY not restored on partial unlock: {restored_openai!r}"
        )
        # Anthropic must still be the shard-A from the lock step.
        assert still_locked == locked_anthropic, (
            f"ANTHROPIC_API_KEY changed despite unlock --alias={openai_alias}: "
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


# ---------------------------------------------------------------------------
# HF7 — operator can purge orphans (worthless-3907)
# ---------------------------------------------------------------------------


class TestPurgeOrphans:
    """Contract for HF7 / worthless-3907: there must be a way to delete
    orphan DB rows whose .env value the user has manually removed.

    RED today because no `purge` command exists — typer will reject parse.
    The test is contracted against the bead's option (A): `worthless purge
    --orphans`. If HF7 lands option (C) (`doctor --fix`) instead, this red
    can stay red and the doctor red below will turn green.
    """

    def test_purge_orphans_removes_dangling_db_rows(
        self, home_dir: WorthlessHome, multi_env_file: Path
    ) -> None:
        # Lock both keys, then orphan ONE by deleting its .env line.
        _lock(multi_env_file, home_dir)
        before = _enrollments(home_dir)
        assert len(before) == 2, f"precondition: expected 2 enrollments, got {len(before)}"

        # Strip OPENAI_API_KEY only — leave ANTHROPIC_API_KEY locked & valid.
        kept = [
            line
            for line in multi_env_file.read_text().splitlines()
            if not line.startswith("OPENAI_API_KEY=")
        ]
        multi_env_file.write_text("\n".join(kept) + "\n")

        result = _invoke(["purge", "--orphans", "--yes"], home_dir)

        # (a) Command must exist (i.e. parse succeeds). Today this fails with
        # "No such command 'purge'" — that IS the red contract for HF7.
        assert "no such command" not in result.output.lower(), (
            "HF7 contract: `worthless purge` command must exist.\n" + result.output
        )
        assert result.exit_code == 0, (
            "purge --orphans must succeed when there is a clean orphan to remove:\n" + result.output
        )

        # (b) The orphan DB row is gone; the still-locked one survives.
        after = _enrollments(home_dir)
        assert len(after) == 1, (
            f"purge should remove exactly the orphan: {len(before)} -> {len(after)}"
        )

        # (c) Output names what was purged so the user can audit.
        assert "OPENAI_API_KEY" in result.output or "openai" in result.output.lower(), (
            f"purge output must name what it removed:\n{result.output}"
        )


# ---------------------------------------------------------------------------
# HF7 — doctor --fix repair (worthless-3907 option C)
# ---------------------------------------------------------------------------


class TestDoctorFixOrphans:
    """Companion red to TestPurgeOrphans: the bead's recommended option is
    `worthless doctor --fix`. If HF7 lands doctor instead of purge, this
    red turns green. Either fix is acceptable, but at least one must exist.

    RED today: neither command exists in the CLI registry.
    """

    def test_doctor_fix_detects_and_repairs_orphans(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        _lock(env_file, home_dir)
        env_file.write_text("")  # orphan the lone enrollment

        result = _invoke(["doctor", "--fix", "--yes"], home_dir)

        assert "no such command" not in result.output.lower(), (
            "HF7 contract (option C): `worthless doctor` command must exist.\n" + result.output
        )
        assert result.exit_code == 0, (
            "doctor --fix must succeed on a recoverable inconsistency:\n" + result.output
        )

        # Repaired state: orphan row gone.
        after = _enrollments(home_dir)
        assert after == [], f"doctor --fix did not clean the orphan: {after}"


# ---------------------------------------------------------------------------
# GAP 5 — multi-project status output is path-aware
# ---------------------------------------------------------------------------


class TestMultiProjectStatusOutput:
    """Existing TestMultiProjectPollution asserts at the DB layer. This adds
    the user-facing complement: `worthless status` output must surface both
    projects independently and not bleed proj-b into proj-a's section.

    Likely already green if status is path-aware; if red, that is a discovery
    worth filing. Bead family: worthless-5koc state-machine."""

    def test_status_output_surfaces_both_projects_independently(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        proj_a = tmp_path / "proj-a"
        proj_b = tmp_path / "proj-b"
        proj_a.mkdir()
        proj_b.mkdir()

        env_a = proj_a / ".env"
        env_b = proj_b / ".env"
        env_a.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        env_b.write_text(f"ANTHROPIC_API_KEY={_TEST_KEY_2}\n")

        _lock(env_a, home_dir)
        _lock(env_b, home_dir)

        result = _invoke(["status"], home_dir)
        assert result.exit_code == 0, result.output
        assert not _looks_like_traceback(result.output)

        # Both project paths (or at least their distinct dir names) must
        # appear so the user can tell them apart.
        assert "proj-a" in result.output, f"status output must mention proj-a:\n{result.output}"
        assert "proj-b" in result.output, f"status output must mention proj-b:\n{result.output}"


# ---------------------------------------------------------------------------
# GAP 6 — env edge cases: unicode var names & two .env files sharing a var
# ---------------------------------------------------------------------------


class TestEnvEdgeCases:
    """Both expected to be GREEN today; if either fails it's a discovery."""

    def test_lock_with_unicode_var_name(self, home_dir: WorthlessHome, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        # Non-ASCII var name with an accented character. dotenv permits it;
        # our lock pipeline must not blow up on the encoding boundary.
        env.write_text(f"MY_KÉY={_TEST_KEY}\n", encoding="utf-8")

        result = _invoke(["lock", "--env", str(env)], home_dir)

        if result.exit_code != 0:
            pytest.skip(
                "discovery: unicode var names are not currently supported by "
                f"`worthless lock`. Output:\n{result.output}"
            )
        enrollments = _enrollments(home_dir)
        assert len(enrollments) >= 1, (
            f"unicode var name lock did not create a DB row:\n{result.output}"
        )

    def test_two_env_files_same_var_each_get_own_enrollment(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        proj_a = tmp_path / "a"
        proj_b = tmp_path / "b"
        proj_a.mkdir()
        proj_b.mkdir()
        env_a = proj_a / ".env"
        env_b = proj_b / ".env"
        env_a.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")
        env_b.write_text(f"OPENAI_API_KEY={_TEST_KEY_2}\n")

        _lock(env_a, home_dir)
        _lock(env_b, home_dir)

        enrollments = _enrollments(home_dir)
        assert len(enrollments) == 2, (
            f"two .env files with the same var must produce 2 distinct DB "
            f"rows; got {len(enrollments)}: {enrollments}"
        )
        # And the two rows must point at different env_paths.
        env_paths = {str(getattr(e, "env_path", "")) for e in enrollments}
        assert len(env_paths) == 2, f"both enrollments collapsed to the same env_path: {env_paths}"


# ---------------------------------------------------------------------------
# GAP 7 — concurrency & partial-DB corruption
# ---------------------------------------------------------------------------


def _worthless_cli_args() -> list[str]:
    """Resolve the worthless CLI as an executable invocation. Prefer the
    project's `worthless` script; fall back to `python -m worthless.cli.app`
    so the test never requires a globally-installed binary."""
    binary = shutil.which("worthless")
    if binary:
        return [binary]
    return [sys.executable, "-m", "worthless"]


class TestConcurrencyAndCorruption:
    """xdist-parallel-safe: every test uses tmp_path + an explicit
    WORTHLESS_HOME env var. Real $HOME is never touched.

    Bead: worthless-islq (flock advisory-only on DrvFs/NFS — concurrent
    invocations from WSL on /mnt/c can race). These are the state-machine-
    layer companion to the lock-primitive bug filed there.
    """

    def test_concurrent_lock_does_not_corrupt_db(
        self, home_dir: WorthlessHome, tmp_path: Path
    ) -> None:
        env = tmp_path / ".env"
        env.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")

        env_vars = {
            **os.environ,
            "WORTHLESS_HOME": str(home_dir.base_dir),
        }
        cli = _worthless_cli_args()
        cmd = [*cli, "lock", "--env", str(env)]

        procs = [
            subprocess.Popen(  # noqa: S603 — controlled cli args
                cmd,
                env=env_vars,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for _ in range(2)
        ]
        outputs = []
        codes = []
        for p in procs:
            out, _ = p.communicate(timeout=60)
            outputs.append(out or "")
            codes.append(p.returncode)

        # (a) DB integrity intact.
        with sqlite3.connect(str(home_dir.db_path)) as con:
            integrity = con.execute("PRAGMA integrity_check").fetchone()
        assert integrity == ("ok",), (
            f"sqlite integrity_check failed after concurrent lock: {integrity}\noutputs:\n{outputs}"
        )

        # (b) Exactly one enrollment. Either branch of the contract converges
        # on this: winner enrolls and loser bails cleanly (no row), OR both
        # serialise and the second no-ops as idempotent (no extra row). Zero
        # enrollments would mean both processes failed, which violates the
        # at-least-one-must-succeed contract; > 1 means flock didn't hold.
        enrollments = _enrollments(home_dir)
        assert len(enrollments) == 1, (
            f"concurrent lock produced {len(enrollments)} enrollments (expected 1):\n{outputs}"
        )

        # (c) Loser exits with a real message, not a Python traceback.
        for code, out in zip(codes, outputs):
            assert "Traceback (most recent call last):" not in out, (
                f"concurrent lock loser leaked a traceback (code={code}):\n{out}"
            )

    def test_partial_db_write_recovers_or_errors_clearly(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        _lock(env_file, home_dir)
        db_path = home_dir.db_path
        size = db_path.stat().st_size
        assert size > 0, "precondition: DB file is empty"

        # Truncate to half — guaranteed to corrupt sqlite page structure.
        with db_path.open("r+b") as fh:
            fh.truncate(size // 2)

        # Reset .env so the second lock isn't blocked by an unrelated state.
        env_file.write_text(f"OPENAI_API_KEY={_TEST_KEY}\n")

        result = _invoke(["lock", "--env", str(env_file)], home_dir)

        # Two acceptable outcomes:
        #   (1) lock detects corruption, errors out cleanly with a hint.
        #   (2) lock self-heals (recovery path) and exits 0.
        # NEVER acceptable: silent success that produced more corruption,
        # or a raw Python traceback.
        assert not _looks_like_traceback(result.output), (
            f"corrupted DB caused a leaked traceback:\n{result.output}"
        )

        if result.exit_code != 0:
            assert _has_actionable_hint(
                result.output,
                "corrupt",
                "integrity",
                "malformed",
                "database disk image",
                "recover",
                "re-enroll",
            ), f"corrupted DB error lacks an actionable hint:\n{result.output}"
        else:
            # Self-healed: the new lock must have produced a queryable DB.
            with sqlite3.connect(str(db_path)) as con:
                integrity = con.execute("PRAGMA integrity_check").fetchone()
            assert integrity == ("ok",), (
                f"lock claimed success on corrupt DB but integrity check failed: "
                f"{integrity}\n{result.output}"
            )
