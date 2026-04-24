"""Restore command — atomically rewrite a ``.env`` with stdin bytes.

Thin Typer wrapper around :func:`worthless.cli.safe_rewrite.safe_restore`
for recovery flows (scripts, CI hooks, ops runbooks) that need to stamp
known-good bytes onto a ``.env`` while bypassing only the DELTA
blowup-ratio gate. Every other invariant — SYMLINK, CONTAINMENT,
BASENAME, SNIFF, SIZE, TOCTOU, PATH_IDENTITY, FILESYSTEM — still fires.

Content is read from stdin; an empty payload refuses the rewrite so
accidental pipe closures cannot zero out a ``.env``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary
from worthless.cli.safe_rewrite import safe_restore


def register_restore_commands(app: typer.Typer) -> None:
    """Register the ``restore`` command on *app*."""

    @app.command()
    @error_boundary
    def restore(
        target: Path = typer.Argument(..., help="Path to the .env file to restore."),
    ) -> None:
        """Restore a ``.env`` from stdin bytes (bypasses DELTA gate only)."""
        new_content = sys.stdin.buffer.read()
        if not new_content:
            raise WorthlessError(
                ErrorCode.UNKNOWN,
                "refusing to restore with empty stdin — pipe replacement bytes in",
            )
        safe_restore(
            target,
            new_content,
            original_user_arg=target,
            repo_root=target.parent,
            allow_outside_repo=True,
        )
