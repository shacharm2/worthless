"""Unlock command — reconstruct keys from shards, restore .env, clean up."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import rewrite_env_key
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.crypto.splitter import reconstruct_key
from worthless.crypto.types import _zero_buf
from worthless.storage.repository import ShardRepository


def _get_home() -> WorthlessHome:
    """Resolve WorthlessHome from WORTHLESS_HOME env var or default."""
    env_home = os.environ.get("WORTHLESS_HOME")
    if env_home:
        return ensure_home(Path(env_home))
    return ensure_home()


def _list_aliases(home: WorthlessHome) -> list[str]:
    """List all enrolled aliases from shard_a directory."""
    if not home.shard_a_dir.exists():
        return []
    return [
        f.name
        for f in home.shard_a_dir.iterdir()
        if f.is_file() and not f.name.endswith(".meta")
    ]


def _unlock_alias(
    alias: str,
    home: WorthlessHome,
    repo: ShardRepository,
    env_path: Path | None,
) -> str | None:
    """Unlock a single alias. Returns the reconstructed key string, or None on error."""
    console = get_console()
    shard_a_path = home.shard_a_dir / alias
    meta_path = home.shard_a_dir / f"{alias}.meta"

    if not shard_a_path.exists():
        raise WorthlessError(ErrorCode.KEY_NOT_FOUND, f"Shard A not found for alias: {alias}")

    # Read shard_a
    shard_a = bytearray(shard_a_path.read_bytes())

    # Fetch shard_b from DB
    stored = asyncio.run(repo.retrieve(alias))
    if stored is None:
        _zero_buf(shard_a)
        raise WorthlessError(ErrorCode.KEY_NOT_FOUND, f"Shard B not found in DB for alias: {alias}")

    try:
        # Reconstruct the key
        key_buf = reconstruct_key(shard_a, stored.shard_b, stored.commitment, stored.nonce)
        try:
            key_str = key_buf.decode()

            # Read metadata for var_name
            var_name = None
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                var_name = meta.get("var_name")

            # Restore .env if it exists and we have var_name
            actual_env = env_path
            if actual_env and actual_env.exists() and var_name:
                rewrite_env_key(actual_env, var_name, key_str)
            elif var_name:
                # .env doesn't exist — print key to stdout as recovery
                console.print_warning(f"No .env file at {actual_env}. Printing key for recovery:")
                sys.stdout.write(f"{var_name}={key_str}\n")
                sys.stdout.flush()
            else:
                # No metadata — print raw key
                console.print_warning(f"No metadata for {alias}. Printing key for recovery:")
                sys.stdout.write(f"{alias}={key_str}\n")
                sys.stdout.flush()

            # Clean up: delete shard_a file, metadata, and DB entry
            shard_a_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            asyncio.run(repo.delete(alias))

            return key_str
        finally:
            _zero_buf(key_buf)
    finally:
        _zero_buf(shard_a)
        stored.zero()


def register_unlock_commands(app: typer.Typer) -> None:
    """Register the unlock command on the Typer app."""

    @app.command()
    def unlock(
        alias: Optional[str] = typer.Option(
            None, "--alias", "-a", help="Specific alias to unlock (default: all)"
        ),
        env: Path = typer.Option(
            Path(".env"), "--env", "-e", help="Path to .env file"
        ),
    ) -> None:
        """Restore original API keys from shards."""
        console = get_console()
        home = _get_home()
        repo = ShardRepository(str(home.db_path), home.fernet_key)

        try:
            if alias:
                _unlock_alias(alias, home, repo, env)
                console.print_success(f"Unlocked {alias}.")
            else:
                aliases = _list_aliases(home)
                if not aliases:
                    console.print_warning("No enrolled keys found.")
                    return
                for a in aliases:
                    _unlock_alias(a, home, repo, env)
                console.print_success(f"{len(aliases)} key(s) restored.")
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
