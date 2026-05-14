"""WOR-464: enrollment rows in BROKEN/UNKNOWN status.

Worthless does not currently persist an explicit enrollment-status
column — health is inferred. An enrollment is "BROKEN" here when its
shard cannot be reconstructed at all:

  * Shard B row exists in the DB but the corresponding shard A file on
    disk (``~/.worthless/shard_a/<key_alias>``) is missing.

  * (Future) Shard B is present but commitment validation fails — not
    detectable without the user's recovery flow, so deferred.

Repair: surgical delete of the enrollment row(s) for the broken alias
plus the dangling DB shard row. Safe: the underlying secret is already
unrecoverable; we're just removing the dead reference so ``worthless
status`` stops surfacing it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from worthless.cli.commands.doctor.checks._helpers import load_enrollments
from worthless.cli.commands.doctor.registry import CheckContext, CheckResult

logger = logging.getLogger(__name__)
check_id = "broken_status"


def _delete_one_alias(ctx: CheckContext, alias: str, rows: list) -> dict | None:
    """Delete all DB rows for *alias*. Return a fixed-entry dict or None on failure."""
    try:
        deleted = sum(
            1
            for row in rows
            if asyncio.run(ctx.repo.delete_enrollment(row.key_alias, row.env_path))
        )
        if deleted:
            return {"key_alias": alias, "rows_deleted": deleted}
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to repair broken enrollment %s: %s",
            alias,
            type(exc).__name__,
        )
        return None


def _repair_broken(ctx: CheckContext, broken: list[str], alias_map: dict) -> list[dict]:
    return [
        entry
        for alias in broken
        for entry in [_delete_one_alias(ctx, alias, alias_map[alias])]
        if entry is not None
    ]


def _build_alias_map(enrollments: list) -> dict[str, list]:
    alias_map: dict[str, list] = {}
    for e in enrollments:
        alias_map.setdefault(e.key_alias, []).append(e)
    return alias_map


def _find_broken(enrollments: list, shard_a_dir) -> list[str]:
    deduped = list({e.key_alias: e for e in enrollments}.values())
    missing = {e.key_alias for e in deduped if not (shard_a_dir / e.key_alias).exists()}
    return sorted(missing)


def _build_summary(n: int) -> str:
    if n == 0:
        return "No broken enrollments."
    return f"{n} enrollment{'s' if n != 1 else ''} in BROKEN status (shard_a missing)"


def _maybe_fix(
    ctx: CheckContext,
    broken: list[str],
    enrollments: list,
    status: Literal["ok", "warn", "error"],
) -> tuple[list[dict], Literal["ok", "warn", "error"]]:
    fixed: list[dict] = []
    if ctx.fix and broken and not ctx.dry_run:
        alias_map = _build_alias_map(enrollments)
        fixed = _repair_broken(ctx, broken, alias_map)
        if len(fixed) == len(broken):
            status = "ok"
    return fixed, status


def run(ctx: CheckContext) -> CheckResult:
    enrollments, err = load_enrollments(ctx, check_id)
    if err is not None:
        return err
    if enrollments is None:  # pragma: no cover — unreachable: load_enrollments always pairs
        raise RuntimeError("load_enrollments returned (None, None) — programming error")

    broken = _find_broken(enrollments, ctx.home.shard_a_dir)

    findings = [{"key_alias": a, "inferred_status": "BROKEN"} for a in broken]
    status = "ok" if not broken else "warn"
    fixed, status = _maybe_fix(ctx, broken, enrollments, status)

    return CheckResult(
        check_id=check_id,
        status=status,
        findings=findings,
        summary=_build_summary(len(broken)),
        fixable=True,
        fixed=fixed,
        skipped_reason=None,
    )
