"""Unlock command -- reconstruct keys from shards, restore .env, clean up."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from dotenv import dotenv_values

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, get_home
from worthless.cli.console import get_console
from worthless.cli.commands.wrap import _PROVIDER_ENV_MAP
from worthless.cli.dotenv_rewriter import remove_env_key, rewrite_env_key
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary
from worthless.crypto.splitter import reconstruct_key, reconstruct_key_fp
from worthless.crypto.types import zero_buf
from worthless.storage.repository import (
    EnrollmentRecord,
    EncryptedShard,
    ShardRepository,
    StoredShard,
)


async def _resolve_enrollment(
    alias: str,
    repo: ShardRepository,
    env_path: Path | None,
) -> EnrollmentRecord | None:
    """Find the enrollment for *alias*, raising on ambiguity."""
    env_str = str(env_path.resolve()) if env_path else None
    if env_str:
        return await repo.get_enrollment(alias, env_str)

    all_enrollments = await repo.list_enrollments(alias)
    if len(all_enrollments) > 1:
        paths = ", ".join(e.env_path or "<direct>" for e in all_enrollments)
        raise WorthlessError(
            ErrorCode.KEY_NOT_FOUND,
            f"Alias {alias!r} is enrolled in multiple env files ({paths}). "
            f"Specify --env to choose which to unlock.",
        )
    return all_enrollments[0] if all_enrollments else None


def _load_shard_a(
    encrypted: EncryptedShard,
    env_path: Path | None,
    var_name: str | None,
    home: WorthlessHome,
    alias: str,
) -> bytearray:
    """Load shard-A from .env (format-preserving) or disk (legacy)."""
    if encrypted.prefix is not None and encrypted.charset is not None:
        if not (env_path and env_path.exists() and var_name):
            raise WorthlessError(
                ErrorCode.KEY_NOT_FOUND,
                f"Cannot unlock {alias}: shard-A is in .env but no valid env_path",
            )
        parsed = dotenv_values(env_path)
        shard_a_value = parsed.get(var_name)
        if shard_a_value is None:
            raise WorthlessError(
                ErrorCode.KEY_NOT_FOUND,
                f"Variable {var_name!r} not found in {env_path}",
            )
        return bytearray(shard_a_value.encode("utf-8"))

    # Legacy: shard_a is a file on disk
    shard_a_path = home.shard_a_dir / alias
    if not shard_a_path.exists():
        raise WorthlessError(
            ErrorCode.KEY_NOT_FOUND,
            f"Shard A not found for alias: {alias}",
        )
    return bytearray(shard_a_path.read_bytes())


def _reconstruct(
    encrypted: EncryptedShard,
    shard_a: bytearray,
    stored: StoredShard,
) -> bytearray:
    """Reconstruct the original key from shards."""
    if encrypted.prefix is not None and encrypted.charset is not None:
        return reconstruct_key_fp(
            shard_a,
            stored.shard_b,
            stored.commitment,
            stored.nonce,
            encrypted.prefix,
            encrypted.charset,
        )
    return reconstruct_key(shard_a, stored.shard_b, stored.commitment, stored.nonce)


async def _unlock_alias(
    alias: str,
    home: WorthlessHome,
    repo: ShardRepository,
    env_path: Path | None,
) -> str | None:
    """Unlock a single alias. Returns the reconstructed key string, or None on error."""
    console = get_console()

    encrypted = await repo.fetch_encrypted(alias)
    if encrypted is None:
        raise WorthlessError(
            ErrorCode.KEY_NOT_FOUND,
            f"Shard B not found in DB for alias: {alias}",
        )

    stored = repo.decrypt_shard(encrypted)
    shard_a: bytearray | None = None

    try:
        enrollment = await _resolve_enrollment(alias, repo, env_path)
        var_name = enrollment.var_name if enrollment else None

        shard_a = _load_shard_a(encrypted, env_path, var_name, home, alias)
        key_buf = _reconstruct(encrypted, shard_a, stored)

        try:
            key_str = key_buf.decode()
            _restore_env(env_path, var_name, key_str, encrypted.provider, alias, console)
            await _cleanup_enrollment(alias, enrollment, repo, home)
            return key_str
        finally:
            zero_buf(key_buf)
    finally:
        if shard_a is not None:
            zero_buf(shard_a)
        if stored is not None:
            stored.zero()


def _restore_env(
    env_path: Path | None,
    var_name: str | None,
    key_str: str,
    provider: str,
    alias: str,
    console,
) -> None:
    """Write the reconstructed key back to .env or print for recovery."""
    if env_path and env_path.exists() and var_name:
        rewrite_env_key(env_path, var_name, key_str)
        base_url_var = _PROVIDER_ENV_MAP.get(provider)
        if base_url_var:
            remove_env_key(env_path, base_url_var)
    elif var_name:
        console.print_warning(f"No .env file at {env_path}. Printing key for recovery:")
        sys.stdout.write(f"{var_name}={key_str}\n")
        sys.stdout.flush()
    else:
        console.print_warning(f"No enrollment for {alias}. Printing key for recovery:")
        sys.stdout.write(f"{alias}={key_str}\n")
        sys.stdout.flush()


async def _cleanup_enrollment(
    alias: str,
    enrollment: EnrollmentRecord | None,
    repo: ShardRepository,
    home: WorthlessHome,
) -> None:
    """Delete enrollment and shard if no other enrollments remain."""
    enrollment_env = enrollment.env_path if enrollment else None
    remaining = await repo.list_enrollments(alias)
    await repo.delete_enrollment(alias, enrollment_env)
    remaining = [e for e in remaining if e.env_path != enrollment_env]

    if not remaining:
        legacy_path = home.shard_a_dir / alias
        legacy_path.unlink(missing_ok=True)
        await repo.delete_enrolled(alias)


def register_unlock_commands(app: typer.Typer) -> None:
    """Register the unlock command on the Typer app."""

    @app.command()
    @error_boundary
    def unlock(
        alias: str | None = typer.Option(
            None, "--alias", "-a", help="Specific alias to unlock (default: all)"
        ),
        env: Path = typer.Option(Path(".env"), "--env", "-e", help="Path to .env file"),
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
                # List aliases from DB, not disk
                aliases = await repo.list_keys()
                if not aliases:
                    console.print_warning("No enrolled keys found.")
                    return
                for a in aliases:
                    await _unlock_alias(a, home, repo, env)
                console.print_success(f"{len(aliases)} key(s) restored.")

        with acquire_lock(home):
            asyncio.run(_unlock_async())
