"""Doctor command — diagnose and repair stuck DB/.env states (HF7 / worthless-3907).

Currently handles TWO known shapes:

1. Orphan DB enrollments whose ``.env`` line was deleted by the user (HF7).
   The dogfood-discovered stuck state from 2026-04-30:

     worthless unlock -> "No enrolled keys found." (silently skips orphan)
     worthless status -> "Enrolled keys: ... PROTECTED" (lists the orphan)

2. OpenClaw integration drift (Phase 2.d / WOR-431): OpenClaw installed
   after ``worthless lock`` ran, or skill folder gone stale. Doctor
   surfaces skill version mismatches and un-wired providers, and can
   reinstall the skill when ``--fix`` is passed.

``worthless doctor`` is read-only: it lists issues.
``worthless doctor --fix`` repairs what it can (destructive for orphans,
safe for skill reinstall). Prompts unless ``--yes``. ``--dry-run`` shows
the planned action without writing.

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
import re
from pathlib import Path

import typer

from worthless.cli.bootstrap import acquire_lock, get_home
from worthless.cli.commands.revoke import _revoke_async
from worthless.cli.console import get_console
from worthless.cli.errors import error_boundary
from worthless.cli.orphans import FIX_PHRASE, PROBLEM_PHRASE, find_orphans, is_orphan
from worthless.openclaw import config as _oc_config
from worthless.openclaw import integration as _oc_integration
from worthless.openclaw import skill as _oc_skill
from worthless.openclaw.errors import OpenclawIntegrationError
from worthless.openclaw.integration import IntegrationState
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


def _print_orphan_lines(
    orphans: list[EnrollmentRecord], *, dry_run: bool, show_fix_hint: bool = True
) -> None:
    """One line per orphan, plain English. Phrase tokens come from
    ``worthless.cli.orphans``. ``typer.echo`` because ``WorthlessConsole``
    only exposes semantic methods (success/error/hint/warning).

    ``show_fix_hint`` is False when we're already running ``--fix`` — no
    point telling the user to run the command they just ran.
    """
    suffix = " (dry-run: no changes)" if dry_run else ""
    for e in orphans:
        typer.echo(
            f"  • {PROBLEM_PHRASE} {e.key_alias}: .env line deleted "
            f"({e.var_name} -> {e.env_path}){suffix}"
        )
    if show_fix_hint:
        typer.echo(f"    fix: run `{FIX_PHRASE}`")


_VERSION_LINE = re.compile(r"^Version:\s*(\S+)\s*$", re.MULTILINE)


def _skill_installed_version(skill_dir: Path) -> str | None:
    """Return the ``Version:`` string from the installed SKILL.md, or None.

    Returns None when the dir / file is absent, unreadable, or has no
    Version line. The parse pattern mirrors :func:`worthless.openclaw.skill.current_version`
    so the comparison in :func:`_check_openclaw_section` is apples-to-apples.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        body = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _VERSION_LINE.search(body)
    return match.group(1) if match else None


def _check_skill(
    state: IntegrationState,
    *,
    fix: bool,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    """Check skill install health. Returns ``(issues, fixed_items)``."""
    issues: list[str] = []
    fixed_items: list[str] = []

    if state.workspace_path is None:
        return ["workspace not found — skill check skipped"], []

    skill_dir = state.workspace_path / "skills" / "worthless"
    installed_ver = _skill_installed_version(skill_dir)
    try:
        bundled_ver = _oc_skill.current_version()
    except OpenclawIntegrationError:
        bundled_ver = None

    skill_needs_repair = installed_ver is None or (
        bundled_ver is not None and installed_ver != bundled_ver
    )
    if installed_ver is None:
        issues.append("skill not installed")
    elif skill_needs_repair:
        issues.append(f"skill stale (installed {installed_ver}, bundled {bundled_ver})")

    if fix and skill_needs_repair:
        if dry_run:
            fixed_items.append("[dry-run] would reinstall skill")
        else:
            try:
                _oc_skill.install(state.workspace_path / "skills")
                fixed_items.append("skill reinstalled")
            except (OpenclawIntegrationError, OSError) as exc:
                issues.append(f"skill repair failed: {exc}")

    return issues, fixed_items


def _check_providers(
    state: IntegrationState,
    healthy: list,
) -> list[str]:
    """Check openclaw.json provider entries for each healthy enrollment.

    Returns a list of issue strings (empty = all wired correctly).
    """
    issues: list[str] = []
    for e in healthy:
        provider_name = f"worthless-{e.provider}"
        if state.config_path is None:
            issues.append(f"{provider_name} not wired (no openclaw.json) — re-run `worthless lock`")
            continue
        try:
            entry = _oc_config.get_provider(state.config_path, provider_name)
        except Exception:
            issues.append(f"{provider_name} config unreadable — re-run `worthless lock`")
            continue
        if entry is None:
            issues.append(f"{provider_name} not wired in openclaw.json — re-run `worthless lock`")
        else:
            actual_url = entry.get("baseUrl", "")
            expected_url = f"http://127.0.0.1:8787/{e.key_alias}/v1"
            if actual_url != expected_url:
                issues.append(
                    f"{provider_name} baseUrl mismatch "
                    f"(got {actual_url!r}, expected {expected_url!r}) — re-run `worthless lock`"
                )
    return issues


def _check_openclaw_section(
    repo: ShardRepository,
    *,
    fix: bool,
    dry_run: bool,
) -> bool:
    """Check OpenClaw health. Print diagnostics; optionally repair skill.

    Returns True when any issue was found (even if ``--fix`` repaired it).
    Returns False and prints nothing when OpenClaw is absent OR all checks
    pass — caller shows "Nothing to fix" in that case.

    Spec: ``.claude/plans/graceful-dreaming-reef.md`` §"Phase 2.d" /
    test matrix rows U-DOC-01..07.
    """
    state = _oc_integration.detect()
    if not state.present:
        return False

    skill_issues, fixed_items = _check_skill(state, fix=fix, dry_run=dry_run)

    try:
        enrollments = asyncio.run(repo.list_enrollments())
    except Exception:
        enrollments = []
        skill_issues.append("could not read enrollment DB — provider check skipped")

    healthy = [e for e in enrollments if not is_orphan(e)]
    provider_issues = _check_providers(state, healthy)

    all_issues = skill_issues + provider_issues
    if not all_issues and not fixed_items:
        return False  # all checks passed, stay silent

    typer.echo("\nOpenClaw:")
    for issue in all_issues:
        typer.echo(f"  ✗ {issue}")
    for item in fixed_items:
        typer.echo(f"  ✓ {item}")
    return True


def _doctor_run(*, fix: bool, yes: bool, dry_run: bool) -> None:
    """Core doctor logic. Checks orphan DB rows AND OpenClaw integration.

    Prints "Nothing to fix" only when both checks pass clean. Each check
    section prints its own diagnostics; ``_doctor_run`` handles the orphan
    purge flow and the final clean-state message.
    """
    console = get_console()
    home = get_home()

    with acquire_lock(home):
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        orphans = asyncio.run(_list_orphans(repo))
        openclaw_issues = _check_openclaw_section(repo, fix=fix, dry_run=dry_run)

        if not orphans and not openclaw_issues:
            console.print_success("Nothing to fix. All locked keys have a matching .env line.")
            return

        if not orphans:
            # OpenClaw issues already printed by _check_openclaw_section.
            return

        plural = "s" if len(orphans) != 1 else ""
        # Parenthetical sidesteps the their/its pronoun mismatch on the
        # singular case ("1 broken record — their..." reads wrong).
        console.print_warning(f"{len(orphans)} broken record{plural} (.env line deleted):")
        _print_orphan_lines(
            orphans,
            dry_run=fix and dry_run,
            # Suppress "fix: run worthless doctor --fix" when we're ALREADY
            # running --fix. Diagnose-only (no --fix) keeps the hint.
            show_fix_hint=not fix,
        )

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
        console.print_success(f"Cleaned up {purged} broken record(s).")


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
