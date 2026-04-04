"""Revoke command -- securely delete an enrolled key (shard_a + DB records)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from worthless.cli.bootstrap import acquire_lock, get_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError, sanitize_exception
from worthless.storage.repository import ShardRepository


async def _revoke_async(alias: str, repo: ShardRepository, shard_a_dir: Path) -> bool:
    """Delete shard, enrollments, spend_log, and shard_a file.

    Returns True if anything was deleted, False if alias not found anywhere.
    """
    shard_a_path = shard_a_dir / alias
    found_anything = False

    # Check DB for the shard
    db_shard = await repo.fetch_encrypted(alias)
    if db_shard is not None:
        found_anything = True

    # Check shard_a file
    shard_a_exists = shard_a_path.exists() and not shard_a_path.is_symlink()
    if shard_a_exists:
        found_anything = True

    if not found_anything:
        return False

    # 1. Delete spend_log entries (no CASCADE from shards table)
    await repo.delete_spend_log(alias)

    # 2. Delete enrollment_config (no CASCADE from shards table)
    await repo.delete_enrollment_config(alias)

    # 3. Delete shard + cascaded enrollments from DB
    await repo.delete_enrolled(alias)

    # 4. Securely delete shard_a: zero contents, then unlink
    if shard_a_exists:
        size = shard_a_path.stat().st_size
        if size > 0:
            fd = os.open(str(shard_a_path), os.O_WRONLY)
            try:
                os.write(fd, b"\x00" * size)
                os.fsync(fd)
            finally:
                os.close(fd)
        shard_a_path.unlink()

    return True


def _revoke_key(alias: str) -> None:
    """Core revoke logic."""
    console = get_console()
    home = get_home()

    with acquire_lock(home):
        repo = ShardRepository(str(home.db_path), home.fernet_key)

        async def _run() -> bool:
            await repo.initialize()
            return await _revoke_async(alias, repo, home.shard_a_dir)

        revoked = asyncio.run(_run())

    if revoked:
        console.print_success(f"Key '{alias}' revoked. Shard and enrollments removed.")
    else:
        console.print_warning(f"Alias '{alias}' not found. Nothing to revoke.")


def register_revoke_commands(app: typer.Typer) -> None:
    """Register the revoke command on the Typer app."""

    @app.command()
    def revoke(
        alias: str = typer.Option(..., "--alias", "-a", help="Alias of the key to revoke"),
    ) -> None:
        """Permanently revoke an enrolled API key (secure deletion)."""
        console = get_console()
        try:
            _revoke_key(alias)
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            console.print_error(WorthlessError(ErrorCode.UNKNOWN, sanitize_exception(exc)))
            raise typer.Exit(code=1) from exc
