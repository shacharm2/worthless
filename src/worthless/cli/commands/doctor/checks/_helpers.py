"""Shared helpers for doctor check modules."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from worthless.cli.commands.doctor.registry import CheckContext, CheckResult


def load_enrollments(
    ctx: CheckContext, check_id: str
) -> tuple[list, None] | tuple[None, CheckResult]:
    """Initialize the repo and return enrollments, or an error CheckResult.

    Returns a 2-tuple:
      - ``(enrollments, None)`` on success
      - ``(None, CheckResult)`` on failure

    Usage::

        enrollments, err = load_enrollments(ctx, check_id)
        if err is not None:
            return err
    """
    from worthless.cli.commands.doctor.registry import CheckResult

    try:
        asyncio.run(ctx.repo.initialize())
        enrollments = asyncio.run(ctx.repo.list_enrollments())
        return enrollments, None
    except Exception as exc:  # noqa: BLE001
        return None, CheckResult(
            check_id=check_id,
            status="error",
            findings=[],
            summary=f"Could not read enrollment DB: {type(exc).__name__}",
            fixable=True,
            fixed=[],
            skipped_reason=None,
        )
