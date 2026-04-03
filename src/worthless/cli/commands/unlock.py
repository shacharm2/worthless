"""Unlock command -- reconstruct keys from shards, restore .env, clean up."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, get_home
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import rewrite_env_key
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.crypto.splitter import reconstruct_key
from worthless.crypto.types import zero_buf
from worthless.storage.repository import ShardRepository


def _list_aliases(home: WorthlessHome) -> list[str]:
    """List all enrolled aliases from shard_a directory."""
    if not home.shard_a_dir.exists():
        return []
    return [
        f.name
        for f in home.shard_a_dir.iterdir()
        if f.is_file()
    ]


async def _unlock_alias(
    alias: str,
    home: WorthlessHome,
    repo: ShardRepository,
    env_path: Path | None,
) -> str | None:
    """Unlock a single alias. Returns the reconstructed key string, or None on error."""
    console = get_console()
    shard_a_path = home.shard_a_dir / alias

    if not shard_a_path.exists():
        raise WorthlessError(
            ErrorCode.KEY_NOT_FOUND,
            f"Shard A not found for alias: {alias}",
        )

    shard_a = bytearray(shard_a_path.read_bytes())
    stored = None

    try:
        stored = await repo.retrieve(alias)
        if stored is None:
            raise WorthlessError(
                ErrorCode.KEY_NOT_FOUND,
                f"Shard B not found in DB for alias: {alias}",
            )

        # Read var_name from DB enrollment -- check for ambiguity
        env_str = str(env_path.resolve()) if env_path else None
        if env_str:
            enrollment = await repo.get_enrollment(alias, env_str)
        else:
            all_enrollments = await repo.list_enrollments(alias)
            if len(all_enrollments) > 1:
                paths = ", ".join(e.env_path or "<direct>" for e in all_enrollments)
                raise WorthlessError(
                    ErrorCode.KEY_NOT_FOUND,
                    f"Alias {alias!r} is enrolled in multiple env files ({paths}). "
                    f"Specify --env to choose which to unlock.",
                )
            enrollment = all_enrollments[0] if all_enrollments else None
        var_name = enrollment.var_name if enrollment else None

        key_buf = reconstruct_key(shard_a, stored.shard_b, stored.commitment, stored.nonce)
        try:
            key_str = key_buf.decode()

            actual_env = env_path
            if actual_env and actual_env.exists() and var_name:
                rewrite_env_key(actual_env, var_name, key_str)
            elif var_name:
                console.print_warning(f"No .env file at {actual_env}. Printing key for recovery:")
                sys.stdout.write(f"{var_name}={key_str}\n")
                sys.stdout.flush()
            else:
                console.print_warning(f"No enrollment for {alias}. Printing key for recovery:")
                sys.stdout.write(f"{alias}={key_str}\n")
                sys.stdout.flush()

            # Delete this specific enrollment using the DB's env_path (handles NULL)
            enrollment_env = enrollment.env_path if enrollment else None
            remaining = await repo.list_enrollments(alias)
            await repo.delete_enrollment(alias, enrollment_env)
            remaining = [e for e in remaining if e.env_path != enrollment_env]

            # Only delete shard + shard_a file when no enrollments remain
            if not remaining:
                shard_a_path.unlink(missing_ok=True)
                await repo.delete_enrolled(alias)

            return key_str
        finally:
            zero_buf(key_buf)
    finally:
        zero_buf(shard_a)
        if stored is not None:
            stored.zero()


def register_unlock_commands(app: typer.Typer) -> None:
    """Register the unlock command on the Typer app."""

    @app.command()
    def unlock(
        alias: str | None = typer.Option(
            None, "--alias", "-a", help="Specific alias to unlock (default: all)"
        ),
        env: Path = typer.Option(
            Path(".env"), "--env", "-e", help="Path to .env file"
        ),
    ) -> None:
        """Restore original API keys from shards."""
        console = get_console()
        home = get_home()
        repo = ShardRepository(str(home.db_path), home.fernet_key)

        async def _unlock_async():
            await repo.initialize()
            if alias:
                await _unlock_alias(alias, home, repo, env)
                console.print_success(f"Unlocked {alias}.")
            else:
                aliases = _list_aliases(home)
                if not aliases:
                    console.print_warning("No enrolled keys found.")
                    return
                for a in aliases:
                    await _unlock_alias(a, home, repo, env)
                console.print_success(f"{len(aliases)} key(s) restored.")

        try:
            with acquire_lock(home):
                asyncio.run(_unlock_async())
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            console.print_error(WorthlessError(ErrorCode.UNKNOWN, str(exc)))
            raise typer.Exit(code=1) from exc
