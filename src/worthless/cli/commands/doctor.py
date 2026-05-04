"""Doctor command — diagnose and repair stuck DB/.env states (HF7 / worthless-3907).

Currently handles ONE known shape: orphan DB enrollments whose ``.env``
line was deleted by the user. The result is the dogfood-discovered stuck
state from 2026-04-30:

  worthless unlock -> "No enrolled keys found." (silently skips orphan)
  worthless status -> "Enrolled keys: ... PROTECTED" (lists the orphan)

``worthless doctor`` is read-only: it lists orphans.
``worthless doctor --fix`` purges them (destructive). Prompts unless
``--yes``. ``--dry-run`` shows the planned action without writing.

Design seams (foreseen extensions, NOT in this PR):

* ``worthless-7db2`` (P3): a SECOND repair shape — partial-state recovery
  when the home dir is intact but the fernet key is missing from every
  source (manual keyring deletion). ``ensure_home`` will surface that
  state and point users here; doctor will need a key-regeneration flow
  guarded against silently destroying access to existing locked secrets.
* ``worthless-57ad`` (P3, post-v0.4): a BYO-key LLM agent diagnoses
  UNKNOWN stuck states using a user-locked enrollment.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from worthless.cli.bootstrap import acquire_lock, get_home
from worthless.cli.commands.revoke import _revoke_async
from worthless.cli.console import get_console
from worthless.cli.errors import error_boundary
from worthless.cli.orphans import FIX_PHRASE, PROBLEM_PHRASE, find_orphans
from worthless.storage.repository import EnrollmentRecord, ShardRepository


async def _list_orphans(repo: ShardRepository) -> list[EnrollmentRecord]:
    """Initialize the repo and return all orphan enrollments. Uses
    ``find_orphans`` so each shared ``.env`` is parsed at most once.
    """
    await repo.initialize()
    enrollments = await repo.list_enrollments()
    return find_orphans(enrollments)


async def _purge_all(
    orphans: list[EnrollmentRecord],
    repo: ShardRepository,
    shard_a_dir: Path,
) -> int:
    """Delete each orphan's enrollment row surgically. If an alias has no
    enrollments left after the delete, also tear down its shard + spend_log
    + config + shard_a file (the full ``_revoke_async`` path).

    CodeRabbit MAJOR (PR #128 review): the previous version called
    ``_revoke_async`` per orphan, which wipes EVERY enrollment for that
    alias — including healthy rows in other ``.env`` files. That's a
    multi-project data-loss bug. Surgical delete by ``(key_alias, env_path)``
    fixes it; per-alias teardown only fires when the alias has nothing left.

    Re-validates the orphan set against the live DB after acquiring the
    lock so stale entries (state shifted between diagnose + confirm) don't
    delete healthy rows.
    """
    current = await repo.list_enrollments()
    still_orphan_keys = {(e.key_alias, e.env_path) for e in find_orphans(current)}

    purged = 0
    aliases_touched: set[str] = set()
    for orphan in orphans:
        if (orphan.key_alias, orphan.env_path) not in still_orphan_keys:
            continue  # state drifted — skip
        if await repo.delete_enrollment(orphan.key_alias, orphan.env_path):
            purged += 1
            aliases_touched.add(orphan.key_alias)

    # For each touched alias: if zero enrollments remain, tear down the
    # alias-level state (shard_b row + spend_log + config + shard_a file).
    after = await repo.list_enrollments()
    aliases_with_remaining = {e.key_alias for e in after}
    for alias in aliases_touched - aliases_with_remaining:
        await _revoke_async(alias, repo, shard_a_dir)

    return purged


def _print_orphan_lines(orphans: list[EnrollmentRecord], *, dry_run: bool) -> None:
    """One line per orphan, plain English. Phrase tokens come from
    ``worthless.cli.orphans``. ``typer.echo`` because ``WorthlessConsole``
    only exposes semantic methods (success/error/hint/warning).
    """
    suffix = " (dry-run: no changes)" if dry_run else ""
    for e in orphans:
        typer.echo(
            f"  • {PROBLEM_PHRASE} {e.key_alias}: .env line deleted "
            f"({e.var_name} -> {e.env_path}){suffix}"
        )
    typer.echo(f"    fix: run `{FIX_PHRASE}`")


def _doctor_run(*, fix: bool, yes: bool, dry_run: bool) -> None:
    """Core doctor logic. Always reports a positive ``no orphan`` line on
    a clean state so callers can grep for it without false negatives.
    """
    console = get_console()
    home = get_home()

    with acquire_lock(home):
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        orphans = asyncio.run(_list_orphans(repo))

        if not orphans:
            console.print_success("Nothing to fix. All locked keys have a matching .env line.")
            return

        console.print_warning(
            f"{len(orphans)} key(s) can't be restored — their .env line was deleted:"
        )
        _print_orphan_lines(orphans, dry_run=fix and dry_run)

        if not fix:
            return  # diagnose-only mode

        if dry_run:
            console.print_hint(
                "dry-run: no changes made. Re-run with `--fix` (without `--dry-run`) to apply."
            )
            return

        if not yes:
            proceed = typer.confirm(
                f"Delete {len(orphans)} orphan DB row(s) and their shard files?",
                default=False,
            )
            if not proceed:
                console.print_hint("Cancelled. No changes made.")
                return

        purged = asyncio.run(_purge_all(orphans, repo, home.shard_a_dir))
        console.print_success(f"Purged {purged} orphan row(s).")


def register_doctor_commands(app: typer.Typer) -> None:
    """Register the doctor command on the Typer app."""

    @app.command()
    @error_boundary
    def doctor(
        fix: bool = typer.Option(
            False,
            "--fix",
            help="Repair orphan DB rows (destructive). Prompts unless --yes.",
        ),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Skip the confirmation prompt for --fix."
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show planned actions without writing."
        ),
    ) -> None:
        """Diagnose and repair stuck DB/.env states (HF7 / worthless-3907)."""
        _doctor_run(fix=fix, yes=yes, dry_run=dry_run)
