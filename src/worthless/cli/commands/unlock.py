"""Unlock command -- reconstruct keys from shards, restore .env, clean up."""

from __future__ import annotations

import asyncio
import logging
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
from worthless.openclaw import integration as _openclaw_integration
from worthless.openclaw.errors import OpenclawIntegrationError
from worthless.storage.repository import (
    EnrollmentRecord,
    EncryptedShard,
    ShardRepository,
    StoredShard,
)

logger = logging.getLogger(__name__)


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
) -> tuple[str, str] | None:
    """Unlock a single alias.

    Returns ``(provider, alias)`` for the just-unlocked enrollment so the
    caller can feed Phase 2.c's :func:`integration.apply_unlock` for
    symmetric OpenClaw cleanup. Returns ``None`` on errors that are
    propagated as :class:`WorthlessError` from the helpers below.
    """
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
            return (encrypted.provider, alias)
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


def _apply_openclaw_unlock(
    unlocked: list[tuple[str, str]],
    console,  # noqa: ANN001 — Console type is opaque from this layer
    home: WorthlessHome,
) -> bool:
    """OpenClaw symmetric undo + sentinel write. Returns ``partial_failure``.

    Symmetric with ``lock.py::_apply_openclaw``. Per L1: never rolls back
    unlock-core. Per L2 (revised 2026-05-08 by the verification gauntlet):
    detected+failed returns True so the caller raises ``typer.Exit(73)``
    AFTER unlock-core's `.env` restoration commits.

    Returns:
        True if detected+failed (caller should exit non-zero post-commit).
        False if all succeeded OR OpenClaw was not detected on this host.

    Side effects:
        Writes ``$WORTHLESS_HOME/last-lock-status.json`` so ``worthless
        status`` reports DEGRADED state across terminal sessions.
    """
    if not unlocked:
        # Nothing to undo on the OpenClaw side — also nothing to record.
        return False
    try:
        result = _openclaw_integration.apply_unlock(aliases=unlocked)
    except OpenclawIntegrationError as exc:
        logger.warning("openclaw apply_unlock raised unexpectedly: %s", exc)
        _emit_openclaw_unlock_failure(console, home, len(unlocked), str(exc))
        return True
    except Exception as exc:  # noqa: BLE001 — last-resort guard for L1
        logger.warning("openclaw apply_unlock raised unexpectedly: %s", exc)
        _emit_openclaw_unlock_failure(console, home, len(unlocked), str(exc))
        return True

    # ---- Classify the result ---------------------------------------------
    if not result.detected:
        # No OpenClaw on this host — record absent, no UI noise.
        _write_unlock_sentinel(home, status="ok", openclaw="absent", alias_count=0, events=())
        return False

    # Trust-fix classification lives on OpenclawApplyResult.has_failure
    # (single-sourced — see integration.py docstring).
    if not result.has_failure:
        if result.providers_set:
            console.print_success(f"[OK] OpenClaw: removed {len(result.providers_set)} provider(s)")
            for provider_name in result.providers_set:
                console.print_hint(f"   • {provider_name}")
        if result.skill_installed:
            console.print_hint("   • ~/.openclaw/workspace/skills/worthless/ — removed")
        _write_unlock_sentinel(
            home,
            status="ok",
            openclaw="ok",
            alias_count=len(result.providers_set),
            events=tuple(e.to_dict() for e in result.events),
        )
        return False

    # Detected + failed: trust-failure path.
    console.print_failure("[FAIL] OpenClaw cleanup did NOT complete.")
    console.print_warning("   Your .env is restored, but worthless-* entries may remain in")
    console.print_warning("   ~/.openclaw/openclaw.json — re-run `worthless unlock` or")
    console.print_warning("   `worthless doctor` to repair.")
    for name, reason in result.providers_skipped:
        console.print_warning(f"   skipped {name} ({reason})")
    for event in result.events:
        if event.level == "error":
            console.print_warning(f"   {event.code.value} — {event.detail}")
    _write_unlock_sentinel(
        home,
        status="partial",
        openclaw="failed",
        alias_count=len(result.providers_set),
        events=tuple(e.to_dict() for e in result.events),
    )
    return True


def _emit_openclaw_unlock_failure(
    console,  # noqa: ANN001
    home: WorthlessHome,
    alias_count: int,
    detail: str,
) -> None:
    """Print [FAIL] block + write partial sentinel for the unexpected-raise path."""
    console.print_failure("[FAIL] OpenClaw cleanup did NOT complete.")
    console.print_warning("   Your .env is restored, but worthless-* entries may remain in")
    console.print_warning("   ~/.openclaw/openclaw.json — repair via:")
    console.print_warning(f"   detail: {detail}")
    console.print_warning("")
    console.print_warning("   Fix:    worthless doctor")
    _write_unlock_sentinel(
        home,
        status="partial",
        openclaw="failed",
        alias_count=alias_count,
        events=({"code": "openclaw.unexpected_raise", "level": "error", "detail": detail},),
    )


def _write_unlock_sentinel(
    home: WorthlessHome,
    *,
    status: str,
    openclaw: str,
    alias_count: int,
    events: tuple[dict[str, str], ...],
) -> None:
    """Best-effort sentinel write. Failure is logged + swallowed."""
    try:
        from worthless.cli.sentinel import write_sentinel

        write_sentinel(
            home.base_dir,
            status=status,
            openclaw=openclaw,
            alias_count=alias_count,
            events=list(events),
        )
    except OSError as exc:
        logger.warning("sentinel write failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — sentinel is best-effort
        logger.warning("sentinel write failed unexpectedly: %s", exc)


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

        async def _unlock_async() -> bool:
            await repo.initialize()
            unlocked: list[tuple[str, str]] = []
            if alias:
                outcome = await _unlock_alias(alias, home, repo, env)
                if outcome is not None:
                    unlocked.append(outcome)
                console.print_success(f"[OK] Unlocked {alias}.")
            else:
                # Scope to aliases enrolled in this specific .env
                env_str = str(env.resolve())
                all_enrollments = await repo.list_enrollments()
                aliases = sorted({e.key_alias for e in all_enrollments if e.env_path == env_str})
                if not aliases:
                    console.print_warning("No enrolled keys found.")
                    return False
                for a in aliases:
                    outcome = await _unlock_alias(a, home, repo, env)
                    if outcome is not None:
                        unlocked.append(outcome)
                console.print_success(f"[OK] {len(aliases)} key(s) restored.")

            # Phase 2.c: OpenClaw symmetric undo. Per L1 in
            # engineering/research/openclaw-WOR-431-phase-2-spec.md, this
            # NEVER aborts unlock-core success. Per L2 (revised 2026-05-08
            # by the verification gauntlet): detected+failed returns
            # partial_failure=True so the caller raises typer.Exit(73)
            # AFTER unlock-core's .env restoration commits.
            return _apply_openclaw_unlock(unlocked, console, home)

        with acquire_lock(home):
            partial_failure = asyncio.run(_unlock_async())

        # Trust-fix (2026-05-08): symmetric with lock — exit non-zero AFTER
        # unlock-core has restored the .env. Sentinel already updated by
        # _apply_openclaw_unlock; the [FAIL] block already printed.
        if partial_failure:
            raise typer.Exit(code=73)
