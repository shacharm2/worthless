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

# 8rqs Phase 8 moved _PROVIDER_ENV_MAP from wrap.py into lock.py
# (wrap is now a passthrough; lock owns BASE_URL ownership). HF4 on main
# pre-dated that move and still imported from wrap.py — that import would
# fail at module-load post-merge. Importing from the new home, lock.py.
from worthless.cli.commands.lock import _PROVIDER_ENV_MAP

# scan_env_keys is used by HF4's per-key messaging logic (the
# "no DB row here" hard-error path further down in this module).
from worthless.cli.dotenv_rewriter import rewrite_env_keys, scan_env_keys
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary
from worthless.cli.orphans import format_orphan_error
from worthless.crypto.splitter import reconstruct_key, reconstruct_key_fp
from worthless.crypto.types import zero_buf
from worthless.storage.repository import (
    EnrollmentRecord,
    EncryptedShard,
    ShardRepository,
    StoredShard,
)


_RECOVERY_LABEL = "<recovery>"


def _format_restored_line(p: _PlannedRestore) -> str:
    """Per-key audit line emitted after a successful restore (HF4)."""
    where = str(p.env_path) if p.env_path is not None else _RECOVERY_LABEL
    return f"Restored {p.var_name or p.alias} ({p.provider}, alias {p.alias}) → {where}"


def _unrecognised_shards(env: Path) -> list[str]:
    """Var names in *env* that look like LLM provider keys but are not enrolled.

    Used by both unlock branches to discriminate "legitimate empty state"
    (warn + exit 0) from "shard-shape values copied from another machine
    with no DB row here" (HF4 hard error). Reuses scan_env_keys so the
    entropy + KEY_PATTERN guards apply — a low-entropy placeholder like
    ``sk-aaaa…`` will not trigger a false-positive hard error.
    """
    return [var_name for var_name, _value, _provider in scan_env_keys(env)]


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
            # Canonical orphan wording lives in ``cli.orphans`` so the same
            # string surfaces in doctor + unlock + future HF5 status/scan.
            raise WorthlessError(
                ErrorCode.KEY_NOT_FOUND,
                format_orphan_error(
                    EnrollmentRecord(key_alias=alias, var_name=var_name, env_path=str(env_path))
                ),
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
        # Per CodeRabbit nitpick: reuse the imported `zero_buf` helper
        # rather than allocating a new bytes object. Slice-assignment leaves
        # the previous secret material live until GC; zero_buf wipes in-place
        # immediately, matching the pattern used in _pass1_reconstruct.
        zero_buf(self.key_buf)


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
        # Per CodeRabbit nitpick: fail loudly on duplicate var_name rather
        # than silently overwrite. Two PlannedRestore entries pointing at the
        # same env var would mean DB inconsistency or planning bug — losing
        # one plaintext silently is worse than aborting the whole batch.
        assert p.var_name not in updates, (
            f"duplicate planned restore for env var {p.var_name} "
            f"(planned aliases: {[entry.alias for entry in planned]})"
        )
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
) -> list[_PlannedRestore]:
    """Transactional multi-alias unlock.

    Returns the planned restores so the caller can emit per-key audit output
    (HF4 / worthless-5u6y). The returned objects have ``key_buf`` already
    zeroed by this function's ``finally`` block; only metadata (alias,
    var_name, provider, env_path) is safe to read post-return.
    """
    console = get_console()
    planned: list[_PlannedRestore] = []
    try:
        await _pass1_reconstruct(aliases, home, repo, env_path, planned)
        if not planned:
            return planned

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
        return planned
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
        env: Path | None = typer.Option(
            None,
            "--env",
            "-e",
            help="Path to .env file (default: ./.env if present)",
        ),
    ) -> None:
        """Restore original API keys from shards.

        Transactional: on any HMAC verification failure, .env is left
        byte-identical and DB rows are not deleted.
        """
        console = get_console()
        home = get_home()
        repo = ShardRepository(str(home.db_path), home.fernet_key)

        # Detect whether the user passed --env explicitly. The HF4
        # discriminator (raise on shard-shape values without DB rows)
        # only fires when the user named a path — running ``unlock``
        # from a directory with someone else's .env should not surprise-
        # error the user with HF4's hard hint. Per worthless-pnn2.
        explicit_env = env is not None
        if env is None:
            env = Path(".env")

        def _raise_unrecognised_shards() -> None:
            # HF4 (worthless-5u6y): if the .env contains values that look
            # like LLM provider keys but have no matching DB row here, the
            # user has unrecoverable shard-A values — fail loudly. Otherwise
            # the .env is genuinely empty and the caller's "no enrolled
            # keys" warning is correct.
            #
            # Pnn2: only run this discriminator when the user explicitly
            # named --env. With the default (CWD ./.env), we cannot tell
            # whether shard-shape values belong to a different project the
            # user is just visiting; warning + exit 0 is the safer default.
            if not explicit_env:
                return
            unrecognised = _unrecognised_shards(env)
            if not unrecognised:
                return
            raise WorthlessError(
                ErrorCode.KEY_NOT_FOUND,
                f"No enrollment found for shard-shape value(s) in {env}: "
                f"{', '.join(unrecognised)}. If this .env was copied from "
                f"another machine, those values are unrecognised shards "
                f"here — re-lock from the original machine, or remove "
                f"them manually if they are junk.",
            )

        async def _unlock_async():
            await repo.initialize()
            if alias:
                planned = await _unlock_batch([alias], home, repo, env)
                if planned:
                    console.print_success(_format_restored_line(planned[0]))
                    return
                # If a typo'd --alias points at a .env full of shard-shape
                # values, silent success is the worst possible feedback.
                _raise_unrecognised_shards()
                console.print_warning(f"Alias not found or no keys restored: {alias}.")
                return

            env_str = str(env.resolve())
            all_enrollments = await repo.list_enrollments()
            aliases = sorted({e.key_alias for e in all_enrollments if e.env_path == env_str})
            if not aliases:
                _raise_unrecognised_shards()
                console.print_warning("No enrolled keys found.")
                return

            planned = await _unlock_batch(aliases, home, repo, env)
            for p in planned:
                console.print_success(_format_restored_line(p))
            n = len(planned)
            if n > 1:
                # Per-key lines already covered N=1; only emit the summary
                # when there's something to count.
                console.print_success(f"{n} key(s) restored.")

        with acquire_lock(home):
            asyncio.run(_unlock_async())
