"""Unlock command -- reconstruct keys from shards, restore .env, clean up.

Transactional across N keys: either every alias is reconstructed and
``.env`` is fully rewritten with plaintext + BASE_URLs removed, or
nothing changes. Mirrors the lock pipeline in ``commands/lock.py``.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

import typer
from dotenv import dotenv_values

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, get_home
from worthless.cli.console import get_console
from worthless.cli.commands.wrap import _PROVIDER_ENV_MAP
from worthless.cli.dotenv_rewriter import rewrite_env_keys
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
    """Reconstruct the original key from shards (raises ShardTamperedError on bad HMAC)."""
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


@dataclass(eq=False)
class _PlannedRestore:
    """One alias's in-flight unlock plan — built pass-1, consumed by pass-2/3."""

    alias: str
    provider: str
    enrollment: EnrollmentRecord | None
    var_name: str | None
    env_path: Path | None
    key_buf: bytearray = field(repr=False)

    def zero(self) -> None:
        self.key_buf[:] = b"\x00" * len(self.key_buf)


async def _pass1_reconstruct(
    aliases: list[str],
    home: WorthlessHome,
    repo: ShardRepository,
    env_path: Path | None,
    planned_out: list[_PlannedRestore],
) -> None:
    """Reconstruct + verify every alias in memory. No .env or DB writes.

    Mutates *planned_out* so the caller's ``finally`` can zero buffers
    even if a later alias raises. On any failure the partial buffers
    are zeroed by the caller; nothing is written to disk.
    """
    for alias in aliases:
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
            planned_out.append(
                _PlannedRestore(
                    alias=alias,
                    provider=encrypted.provider,
                    enrollment=enrollment,
                    var_name=var_name,
                    env_path=env_path,
                    key_buf=key_buf,
                )
            )
        finally:
            if shard_a is not None:
                zero_buf(shard_a)
            stored.zero()


def _batch_restore_env(env_path: Path, planned: list[_PlannedRestore]) -> None:
    """One ``rewrite_env_keys`` call: restore plaintext + drop BASE_URLs."""
    updates: dict[str, str] = {}
    removals: set[str] = set()
    for p in planned:
        if p.var_name is None:
            continue
        updates[p.var_name] = p.key_buf.decode("utf-8")
        base_url_var = _PROVIDER_ENV_MAP.get(p.provider)
        if base_url_var:
            removals.add(base_url_var)

    if not updates and not removals:
        return

    rewrite_env_keys(env_path, updates, removals=removals or None)


async def _pass3_db_cleanup(
    repo: ShardRepository, home: WorthlessHome, planned: list[_PlannedRestore]
) -> None:
    """Delete enrollments + shards. Runs only after .env rewrite succeeds.

    A crash between rewrite and cleanup leaves orphan DB rows the user
    can re-process by re-running ``worthless unlock`` (idempotent: the
    next run finds the .env already plaintext, the alias still in DB,
    and treats it as a fresh unlock attempt).
    """
    for p in planned:
        enrollment_env = p.enrollment.env_path if p.enrollment else None
        remaining = await repo.list_enrollments(p.alias)
        await repo.delete_enrollment(p.alias, enrollment_env)
        remaining = [e for e in remaining if e.env_path != enrollment_env]
        if not remaining:
            (home.shard_a_dir / p.alias).unlink(missing_ok=True)
            await repo.delete_enrolled(p.alias)


def _print_recovery_keys(planned: list[_PlannedRestore], console) -> None:
    """Print keys for aliases with no env_path (recovery mode)."""
    for p in planned:
        if p.env_path is not None and p.var_name is not None:
            continue
        if p.var_name:
            console.print_warning(f"No .env file at {p.env_path}. Printing key for recovery:")
            sys.stdout.write(f"{p.var_name}={p.key_buf.decode('utf-8')}\n")
        else:
            console.print_warning(f"No enrollment for {p.alias}. Printing key for recovery:")
            sys.stdout.write(f"{p.alias}={p.key_buf.decode('utf-8')}\n")
    sys.stdout.flush()


async def _unlock_batch(
    aliases: list[str],
    home: WorthlessHome,
    repo: ShardRepository,
    env_path: Path | None,
) -> int:
    """Transactional multi-alias unlock. Returns count restored."""
    console = get_console()
    planned: list[_PlannedRestore] = []
    try:
        await _pass1_reconstruct(aliases, home, repo, env_path, planned)
        if not planned:
            return 0

        env_writers = [p for p in planned if p.env_path is not None and p.var_name is not None]
        if env_writers:
            # Refuse pass-3 if the .env we were supposed to restore into is gone.
            # Otherwise we'd zero plaintext, drop DB rows, and never write or print
            # the key — silent permanent loss.
            if env_path is None or not env_path.exists():
                raise WorthlessError(
                    ErrorCode.KEY_NOT_FOUND,
                    f"Cannot restore plaintext to missing .env at {env_path}; "
                    f"refusing to delete DB rows. Re-create the file (touch it) "
                    f"or pass --env pointing at the correct path, then re-run.",
                )
            _batch_restore_env(env_path, env_writers)

        _print_recovery_keys(planned, console)

        await _pass3_db_cleanup(repo, home, planned)
        return len(planned)
    finally:
        for p in planned:
            p.zero()


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
        """Restore original API keys from shards.

        Transactional: on any HMAC verification failure, .env is left
        byte-identical and DB rows are not deleted.
        """
        console = get_console()
        home = get_home()
        repo = ShardRepository(str(home.db_path), home.fernet_key)

        async def _unlock_async():
            await repo.initialize()
            if alias:
                count = await _unlock_batch([alias], home, repo, env)
                if count:
                    console.print_success(f"Unlocked {alias}.")
                return

            env_str = str(env.resolve())
            all_enrollments = await repo.list_enrollments()
            aliases = sorted({e.key_alias for e in all_enrollments if e.env_path == env_str})
            if not aliases:
                console.print_warning("No enrolled keys found.")
                return
            count = await _unlock_batch(aliases, home, repo, env)
            if count:
                console.print_success(f"{count} key(s) restored.")

        with acquire_lock(home):
            asyncio.run(_unlock_async())
