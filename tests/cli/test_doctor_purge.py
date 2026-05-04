"""HF7 / worthless-3907: ``worthless doctor --fix`` purges orphan DB rows.

Five RED tests that pin the contract for HF7. Each one currently fails
because the ``doctor`` command does not yet exist — Typer returns exit
code 2 ("No such command 'doctor'") for every invocation. Removing the
xfail markers below is the GREEN gate for HF7.

Discovered live in 2026-04-30 v0.3.2 dogfood:
  * ``worthless unlock`` -> "No enrolled keys found."
  * ``worthless status`` -> "Enrolled keys: openai-622ca1a2 ... PROTECTED"
  * Same DB, opposite answers — orphan DB row, no recovery command.

HF7 contract:
  1. ``worthless doctor`` (no flags): diagnose-only, lists orphans, exit 0,
     does NOT mutate state.
  2. ``worthless doctor --fix``: destructive. Prompts unless ``--yes``.
     Deletes orphan rows + their shard files.
  3. ``worthless doctor --fix --dry-run``: prints planned deletions, no writes.
  4. ``worthless doctor --fix --yes``: skip prompt, perform deletion.

Phrase-token contract (per the HF4/PR #123 review note in worthless-3907):
  Canonical wording for the orphan condition is:
    "alias is orphaned"          (the diagnosis)
    "worthless doctor --fix"     (the actionable suggestion)
  Both phrases must appear in any user-facing message that mentions an
  orphan row. Required-via-AND, not OR-of-five-variants — that's the
  bug class HF4 flagged when pytest's tmp_path embedded "orphan" into
  the test name and any error echoing the path back read as a hit.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome

from tests.conftest import make_repo as _repo
from tests.helpers import fake_openai_key

runner = CliRunner()

_TEST_KEY = fake_openai_key()


# ---------------------------------------------------------------------------
# Helpers (mirror tests/cli/test_state_machine.py — keep cross-file conventions)
# ---------------------------------------------------------------------------


def _invoke(args: list[str], home: WorthlessHome, **kwargs: object) -> object:
    """Run a CLI command with WORTHLESS_HOME pointed at the test home."""
    return runner.invoke(
        app,
        args,
        env={"WORTHLESS_HOME": str(home.base_dir)},
        **kwargs,
    )


def _lock(env_file: Path, home: WorthlessHome) -> None:
    """Lock the env file. Failures here are pre-conditions, not under test."""
    result = _invoke(["lock", "--env", str(env_file)], home)
    assert result.exit_code == 0, f"precondition lock failed:\n{result.output}"


def _looks_like_traceback(text: str) -> bool:
    return "Traceback (most recent call last):" in text


def _has_all_tokens(text: str, *required: str) -> bool:
    """ALL tokens must appear (case-insensitive). Binds output to a phrase
    contract, preventing tmp_path-name false-positives."""
    lowered = text.lower()
    return all(k.lower() in lowered for k in required)


def _enrollments(home: WorthlessHome) -> list:
    """Return DB enrollments. Initialize is idempotent (CREATE … IF NOT EXISTS)."""
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


# ---------------------------------------------------------------------------
# The 5 RED contract tests
# ---------------------------------------------------------------------------


class TestDoctorOrphanPurge:
    """``worthless doctor`` diagnose + ``--fix`` purges orphan DB rows.

    Setup helper: ``_orphan`` locks a key (creating a DB row) and then
    deletes the .env line, leaving the DB row referencing an alias that
    no longer has a matching .env entry — the "orphan" state.
    """

    def _orphan(self, env_file: Path, home: WorthlessHome) -> None:
        _lock(env_file, home)
        env_file.write_text("")  # user manually deleted the locked line

    # ---- 1. Empty / no-orphans path -----------------------------------------

    @pytest.mark.xfail(
        strict=True,
        reason="RED: HF7 (worthless-3907) — `worthless doctor` not yet implemented. "
        "Remove this marker when doctor.py + app.py registration land.",
    )
    def test_doctor_no_orphans_exits_clean(self, home_dir: WorthlessHome) -> None:
        """Fresh home, no enrollments — doctor reports nothing-to-fix and exits 0."""
        result = _invoke(["doctor"], home_dir)

        assert result.exit_code == 0, f"doctor exited non-zero:\n{result.output}"
        assert not _looks_like_traceback(result.output)
        # Bind to a positive-state phrase so a Typer "no such command" error
        # (which ALSO exits non-zero with no traceback) cannot pass this.
        assert _has_all_tokens(result.output, "no orphan"), (
            f"doctor on empty DB must say no orphans found:\n{result.output}"
        )

    # ---- 2. Diagnose mode (no --fix) is read-only ---------------------------

    @pytest.mark.xfail(
        strict=True,
        reason="RED: HF7 (worthless-3907) — `worthless doctor` not yet implemented.",
    )
    def test_doctor_detects_orphan_in_diagnose_mode(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """Diagnose-only mode: lists orphan, exit 0, DB unchanged."""
        self._orphan(env_file, home_dir)
        before = _enrollments(home_dir)
        assert len(before) == 1, "precondition: orphan row exists in DB"

        result = _invoke(["doctor"], home_dir)

        assert result.exit_code == 0, (
            "diagnose-only must NOT exit non-zero — it's a read.\n" + result.output
        )
        assert not _looks_like_traceback(result.output)
        # Phrase-token contract: "alias is orphaned" + actionable suggestion.
        assert _has_all_tokens(result.output, "alias is orphaned", "worthless doctor --fix"), (
            "doctor must use canonical orphan wording AND name the fix command:\n" + result.output
        )
        # Read-only invariant: no DB rows deleted.
        after = _enrollments(home_dir)
        assert len(after) == 1, "diagnose-only mode must NOT mutate state — DB row was deleted."

    # ---- 3. --fix --yes actually purges the orphan --------------------------

    @pytest.mark.xfail(
        strict=True,
        reason="RED: HF7 (worthless-3907) — `worthless doctor --fix` not yet implemented.",
    )
    def test_doctor_fix_yes_purges_orphan(self, home_dir: WorthlessHome, env_file: Path) -> None:
        """`doctor --fix --yes`: skip prompt, purge orphan, DB empty after."""
        self._orphan(env_file, home_dir)
        assert len(_enrollments(home_dir)) == 1, "precondition: orphan row exists"

        result = _invoke(["doctor", "--fix", "--yes"], home_dir)

        assert result.exit_code == 0, f"doctor --fix --yes failed:\n{result.output}"
        assert not _looks_like_traceback(result.output)
        # DB row gone.
        after = _enrollments(home_dir)
        assert len(after) == 0, f"orphan DB row was NOT purged. Remaining: {after}\n{result.output}"

    # ---- 4. --fix --dry-run lists planned deletions, writes nothing ---------

    @pytest.mark.xfail(
        strict=True,
        reason="RED: HF7 (worthless-3907) — `--dry-run` flag not yet implemented.",
    )
    def test_doctor_fix_dry_run_does_not_write(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """`doctor --fix --dry-run`: shows planned action, leaves DB intact."""
        self._orphan(env_file, home_dir)

        result = _invoke(["doctor", "--fix", "--dry-run"], home_dir)

        assert result.exit_code == 0, f"dry-run exited non-zero:\n{result.output}"
        assert not _looks_like_traceback(result.output)
        # Output names the deletion as planned, not done.
        assert _has_all_tokens(result.output, "dry-run"), (
            f"dry-run output must mark itself as a preview:\n{result.output}"
        )
        # DB unchanged.
        after = _enrollments(home_dir)
        assert len(after) == 1, f"--dry-run wrote to DB (it MUST NOT). Remaining: {after}"

    # ---- 5. --fix without --yes prompts; declining aborts -------------------

    @pytest.mark.xfail(
        strict=True,
        reason="RED: HF7 (worthless-3907) — interactive prompt not yet implemented.",
    )
    def test_doctor_fix_without_yes_prompts_then_aborts(
        self, home_dir: WorthlessHome, env_file: Path
    ) -> None:
        """`doctor --fix` (no --yes): prompts, "n" answer aborts, DB unchanged."""
        self._orphan(env_file, home_dir)

        # Feed "n" to the confirmation prompt.
        result = _invoke(["doctor", "--fix"], home_dir, input="n\n")

        # Aborted user-decline is not an error; exit 0 with a "cancelled" message.
        assert result.exit_code == 0, (
            f"declining the prompt should be exit 0, not an error:\n{result.output}"
        )
        assert not _looks_like_traceback(result.output)
        # DB still has the orphan — abort means nothing got deleted.
        after = _enrollments(home_dir)
        assert len(after) == 1, f"declining the prompt must NOT delete anything. Remaining: {after}"
