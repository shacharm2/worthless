"""Doctor command — diagnose and repair stuck DB/.env states (HF7 / worthless-3907).

Currently handles ONE known shape: orphan DB enrollments whose ``.env``
line was deleted by the user. The result is the dogfood-discovered stuck
state from 2026-04-30:

  worthless unlock -> "No enrolled keys found." (silently skips orphan)
  worthless status -> "Enrolled keys: ... PROTECTED" (lists the orphan)

``worthless doctor`` is read-only: it lists orphans.
``worthless doctor --fix`` purges them (destructive). Prompts unless
``--yes``. ``--dry-run`` shows the planned action without writing.

Future extension (worthless-57ad, post-v0.4): a small BYO-key LLM agent
diagnoses UNKNOWN stuck states using a user-locked enrollment. Out of
scope here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from dotenv import dotenv_values

from worthless.cli.bootstrap import acquire_lock, get_home
from worthless.cli.commands.revoke import _revoke_async
from worthless.cli.console import get_console
from worthless.cli.errors import error_boundary
from worthless.storage.repository import EnrollmentRecord, ShardRepository


def _is_orphan(enrollment: EnrollmentRecord) -> bool:
    """An enrollment is orphan iff its ``env_path`` is set but the matching
    ``var_name`` line is missing from that file (or the file no longer
    exists). ``env_path is None`` means a direct enrollment with no
    ``.env`` binding — not an orphan, just unbound.
    """
    if not enrollment.env_path:
        return False
    env_path = Path(enrollment.env_path)
    if not env_path.exists():
        return True
    return enrollment.var_name not in dotenv_values(env_path)


async def _list_orphans(repo: ShardRepository) -> list[EnrollmentRecord]:
    """Initialize the repo and return all orphan enrollments."""
    await repo.initialize()
    enrollments = await repo.list_enrollments()
    return [e for e in enrollments if _is_orphan(e)]


async def _purge_all(
    orphans: list[EnrollmentRecord],
    repo: ShardRepository,
    shard_a_dir: Path,
) -> int:
    """Delete each orphan's DB row + shard_a file. Returns count purged."""
    purged = 0
    for e in orphans:
        if await _revoke_async(e.key_alias, repo, shard_a_dir):
            purged += 1
    return purged


def _print_orphan_lines(orphans: list[EnrollmentRecord], *, dry_run: bool) -> None:
    """One line per orphan, using the canonical phrase-token contract.

    Required tokens (per HF4/PR #123 review): ``alias is orphaned`` +
    ``worthless doctor --fix``. Both must appear so callers can grep the
    output deterministically.

    Plain ``typer.echo`` rather than a semantic console method — these
    are body lines, not status. ``WorthlessConsole`` only exposes the
    semantic vocabulary (success/error/hint/warning), not bare print.
    """
    suffix = " (dry-run: no changes)" if dry_run else ""
    for e in orphans:
        typer.echo(f"  • alias is orphaned: {e.key_alias} ({e.var_name} -> {e.env_path}){suffix}")
    typer.echo("    fix: run `worthless doctor --fix`")


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
            console.print_success("no orphan DB rows found. Nothing to fix.")
            return

        console.print_warning(f"{len(orphans)} orphan DB row(s) detected:")
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
