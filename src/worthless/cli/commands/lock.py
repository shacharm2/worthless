"""Lock command -- scan .env, split keys (format-preserving), store shards."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import typer

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, get_home
from worthless.cli.commands.up import _resolve_port
from worthless.cli.commands.wrap import _PROVIDER_ENV_MAP
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import (
    build_enrolled_locations,
    rewrite_env_keys,
    scan_env_keys,
)
from worthless.cli.errors import (
    ErrorCode,
    UnsafeReason,
    UnsafeRewriteRefused,
    WorthlessError,
    error_boundary,
    sanitize_exception,
)
from worthless.cli.key_patterns import detect_prefix
from worthless.crypto.splitter import (
    _verify_commitment,  # noqa: PLC2701 — intentional internal use for re-lock guard
    derive_shard_a_fp,
    reconstruct_key_fp,
    split_key_fp,
)
from worthless.crypto.types import zero_buf
from worthless.exceptions import ShardTamperedError
from worthless.openclaw import integration as _openclaw_integration
from worthless.openclaw.errors import OpenclawIntegrationError
from worthless.storage.repository import ShardRepository, StoredShard

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = frozenset(_PROVIDER_ENV_MAP.keys())
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _make_alias(provider: str, api_key: str) -> str:
    """Deterministic alias: provider + first 8 hex chars of sha256(key)."""
    digest = hashlib.sha256(bytearray(api_key.encode())).hexdigest()[:8]  # nosec B303 -- non-cryptographic fingerprint
    return f"{provider}-{digest}"


def _proxy_base_url(alias: str) -> str:
    """Build the proxy BASE_URL for a given alias."""
    return f"http://127.0.0.1:{_resolve_port(None)}/{alias}/v1"


@dataclass(eq=False)
class _PlannedUpdate:
    """One key's in-flight lock plan — built pass-1, consumed by hook + unwind."""

    alias: str
    var_name: str
    env_path_str: str
    provider: str
    shard_a: bytearray
    shard_b: bytearray
    commitment: bytearray
    nonce: bytearray
    prefix: str
    charset: str
    was_fresh_enroll: bool

    def zero(self) -> None:
        for buf in (self.shard_a, self.shard_b, self.commitment, self.nonce):
            buf[:] = b"\x00" * len(buf)


def _build_verify_hook(planned: list[_PlannedUpdate]):
    """Return a zero-arg hook that raises on any shard round-trip failure.

    Fires inside ``safe_rewrite`` after the tmp fsync but before the atomic
    rename — see ``safe_rewrite._hook_before_replace``. Raising aborts the
    rename, leaving ``.env`` byte-identical.
    """

    def _hook() -> None:
        for p in planned:
            reconstructed: bytearray | None = None
            try:
                reconstructed = reconstruct_key_fp(
                    p.shard_a,
                    p.shard_b,
                    p.commitment,
                    p.nonce,
                    p.prefix,
                    p.charset,
                )
            except ShardTamperedError:
                # from None: drop traceback chain holding shard-material locals.
                raise UnsafeRewriteRefused(UnsafeReason.VERIFY_FAILED) from None
            except ValueError as exc:
                raise WorthlessError(
                    ErrorCode.SHARD_STORAGE_FAILED,
                    sanitize_exception(exc, generic="shard reconstruction malformed"),
                ) from None
            except Exception:  # noqa: BLE001 — preserve opaque refusal contract
                raise UnsafeRewriteRefused(UnsafeReason.VERIFY_FAILED) from None
            finally:
                if reconstructed is not None:
                    zero_buf(reconstructed)

    return _hook


async def _pass1_db_writes(
    repo: ShardRepository,
    candidates: list[tuple[str, str, str]],
    env_str: str,
    token_budget_daily: int | None,
    planned_out: list[_PlannedUpdate],
) -> None:
    """Do every DB write; append each ``_PlannedUpdate`` to *planned_out*.

    MUTATES *planned_out* so partial-failure paths still expose the
    bytearrays the caller's ``finally`` needs to zero.
    """
    for var_name, value, provider in candidates:
        if provider not in _SUPPORTED_PROVIDERS:
            get_console().print_warning(
                f"Skipping {var_name}: provider {provider!r} not yet supported"
            )
            continue

        alias = _make_alias(provider, value)
        db_shard = await repo.fetch_encrypted(alias)

        if db_shard is not None:
            if not db_shard.prefix or not db_shard.charset:
                raise WorthlessError(
                    ErrorCode.SHARD_STORAGE_FAILED,
                    f"Alias {alias!r} predates format-preserving split. "
                    "Run `worthless unlock --all` then re-lock this .env.",
                )
            stored_decrypted = repo.decrypt_shard(db_shard)
            verify_payload = bytearray(value.encode("utf-8"))
            try:
                try:
                    _verify_commitment(
                        verify_payload,
                        stored_decrypted.commitment,
                        stored_decrypted.nonce,
                    )
                except ShardTamperedError as exc:
                    raise WorthlessError(
                        ErrorCode.SHARD_STORAGE_FAILED,
                        f"Alias {alias!r} exists but {var_name} does not match "
                        "the originally-locked key (commitment mismatch).",
                    ) from exc

                derived_shard_a = derive_shard_a_fp(
                    value,
                    stored_decrypted.shard_b,
                    db_shard.prefix,
                    db_shard.charset,
                )
                await repo.add_enrollment(alias, var_name=var_name, env_path=env_str)
                planned_out.append(
                    _PlannedUpdate(
                        alias=alias,
                        var_name=var_name,
                        env_path_str=env_str,
                        provider=provider,
                        shard_a=derived_shard_a,
                        shard_b=bytearray(stored_decrypted.shard_b),
                        commitment=bytearray(stored_decrypted.commitment),
                        nonce=bytearray(stored_decrypted.nonce),
                        prefix=db_shard.prefix,
                        charset=db_shard.charset,
                        was_fresh_enroll=False,
                    )
                )
            finally:
                zero_buf(verify_payload)
                stored_decrypted.zero()
            continue

        # Fresh enroll
        try:
            prefix = detect_prefix(value, provider)
        except ValueError:
            prefix = ""
        sr = split_key_fp(value, prefix, provider)
        try:
            stored = StoredShard(
                shard_b=sr.shard_b,
                commitment=sr.commitment,
                nonce=sr.nonce,
                provider=provider,
            )
            await repo.store_enrolled(
                alias,
                stored,
                var_name=var_name,
                env_path=env_str,
                token_budget_daily=token_budget_daily,
                prefix=sr.prefix,
                charset=sr.charset,
            )
            planned_out.append(
                _PlannedUpdate(
                    alias=alias,
                    var_name=var_name,
                    env_path_str=env_str,
                    provider=provider,
                    shard_a=bytearray(sr.shard_a),
                    shard_b=bytearray(sr.shard_b),
                    commitment=bytearray(sr.commitment),
                    nonce=bytearray(sr.nonce),
                    prefix=sr.prefix,
                    charset=sr.charset,
                    was_fresh_enroll=True,
                )
            )
        finally:
            sr.zero()


def _batch_rewrite(
    env_path: Path,
    planned: list[_PlannedUpdate],
    keys_only: bool,
    existing_env_keys: set[str],
) -> None:
    """One ``safe_rewrite`` call for every planned update + BASE_URL additions."""
    updates: dict[str, str] = {p.var_name: p.shard_a.decode("utf-8") for p in planned}
    additions: dict[str, str] = {}
    if not keys_only:
        for p in planned:
            base_url_var = _PROVIDER_ENV_MAP.get(p.provider)
            if (
                base_url_var
                and base_url_var not in updates
                and base_url_var not in existing_env_keys
                and base_url_var not in additions
            ):
                additions[base_url_var] = _proxy_base_url(p.alias)

    rewrite_env_keys(
        env_path,
        updates,
        additions=additions or None,
        _hook_before_replace=_build_verify_hook(planned),
    )


async def _compensating_unwind(
    repo: ShardRepository, planned: list[_PlannedUpdate]
) -> list[Exception]:
    """Best-effort rollback of pass-1 DB writes. Returns list of failures."""
    errors: list[Exception] = []
    for p in reversed(planned):
        try:
            await repo.delete_enrollment(p.alias, p.env_path_str)
            if p.was_fresh_enroll:
                # Fresh-enroll created this shard + its only enrollment in this
                # same pass — delete_enrollment above just removed the last one.
                await repo.delete_enrolled(p.alias)
        except Exception as exc:  # noqa: BLE001 — keep unwinding subsequent aliases
            logger.debug("Unwind failed for alias %s", p.alias, exc_info=True)
            errors.append(exc)
    return errors


def _apply_openclaw(
    planned: list[_PlannedUpdate],
    console,  # noqa: ANN001 — Console type is opaque from this layer
    quiet: bool,
    home: WorthlessHome,
) -> bool:
    """OpenClaw integration call + sentinel write. Returns ``partial_failure``.

    Per L1 in ``engineering/research/openclaw-WOR-431-phase-2-spec.md``:
    failures here NEVER roll back lock-core. Per L2 (revised 2026-05-08
    by the verification gauntlet): when OpenClaw is **detected** AND the
    integration stage fails, the user is in a false-invariant state ("lock
    succeeded but my agent traffic isn't gated"). Caller (`_lock_keys`)
    raises ``typer.Exit(73)`` AFTER lock-core's `.env`/DB writes are
    fully committed — the binding contract is preserved, but the user
    learns about the partial failure unmissably.

    Returns:
        True if detected+failed (caller should exit non-zero post-commit).
        False if all succeeded OR OpenClaw was not detected on this host.

    Side effects:
        Writes ``$WORTHLESS_HOME/last-lock-status.json`` so ``worthless
        status`` can report DEGRADED state across terminal sessions.
        Sentinel write failure is itself best-effort (logged, swallowed).
    """
    triples: list[tuple[str, str, str]] = [
        (p.provider, p.alias, p.shard_a.decode("utf-8")) for p in planned
    ]
    # Plumb the SAME port lock just used for .env's BASE_URL vars so
    # openclaw.json's baseUrl matches a non-default --port. Without this,
    # users on non-default ports got a wrong baseUrl in openclaw.json
    # while .env's BASE_URL was correct (split-brain proxy URL).
    proxy_base_url = f"http://127.0.0.1:{_resolve_port(None)}"
    try:
        result = _openclaw_integration.apply_lock(
            planned_updates=triples, proxy_base_url=proxy_base_url
        )
    except OpenclawIntegrationError as exc:
        # apply_lock's contract is "never raise". If it does, treat as
        # detected+failed (we don't know which, but the user is in a
        # genuinely broken state — surface it loudly).
        logger.warning("openclaw apply_lock raised unexpectedly: %s", exc)
        _emit_openclaw_failure(console, quiet, home, len(planned), str(exc))
        return True
    except Exception as exc:  # noqa: BLE001 — last-resort guard for L1
        logger.warning("openclaw apply_lock raised unexpectedly: %s", exc)
        _emit_openclaw_failure(console, quiet, home, len(planned), str(exc))
        return True

    # ---- Classify the result ---------------------------------------------
    if not result.detected:
        # No OpenClaw on this host — sentinel reflects "absent", not failure.
        _write_lock_sentinel(home, status="ok", openclaw="absent", alias_count=0, events=())
        return False

    # Trust-fix classification (refined post-test): only ``error``-level
    # events count as failure. ``provider_conflict`` (warn) means the user
    # configured the provider themselves and we respected it — that's a
    # CLEAN state, not a partial failure. ``symlink_refused`` IS error-level
    # because the user's home is in a genuinely unsafe state. ``config_missing``
    # on unlock is warn-level (idempotent no-op).
    has_failure = any(e.level == "error" for e in result.events)

    if not has_failure:
        # Fully successful integration — record OK + enumerate to user.
        if not quiet:
            console.print_success("[OK] OpenClaw integration:")
            for provider_name in result.providers_set:
                console.print_hint(
                    f"   • ~/.openclaw/openclaw.json — added provider '{provider_name}'"
                )
            if result.skill_installed:
                console.print_hint("   • ~/.openclaw/workspace/skills/worthless/ — installed skill")
            console.print_hint("   • Undo: worthless unlock")
        _write_lock_sentinel(
            home,
            status="ok",
            openclaw="ok",
            alias_count=len(result.providers_set),
            events=tuple(_event_to_dict(e) for e in result.events),
        )
        return False

    # Detected + failed: the trust-failure path. Print [FAIL] block, write
    # sentinel as partial. Caller raises typer.Exit(73) after lock-core's
    # .env/DB writes finish committing.
    if not quiet:
        console.print_failure("[FAIL] OpenClaw integration did NOT complete.")
        console.print_warning("   Your .env is locked, but OpenClaw is still calling the")
        console.print_warning("   provider directly with the unsplit key.")
        for name, reason in result.providers_skipped:
            console.print_warning(f"   skipped {name} ({reason})")
        for event in result.events:
            if event.level == "error":
                console.print_warning(f"   {event.code.value} — {event.detail}")
        console.print_warning("")
        console.print_warning("   Fix:       worthless doctor")
        console.print_warning("   Roll back: worthless unlock")
    _write_lock_sentinel(
        home,
        status="partial",
        openclaw="failed",
        alias_count=len(result.providers_set),
        events=tuple(_event_to_dict(e) for e in result.events),
    )
    return True


def _event_to_dict(event) -> dict[str, str]:  # noqa: ANN001
    """Serialize an OpenclawIntegrationEvent for sentinel/JSON output."""
    return {
        "code": event.code.value,
        "level": event.level,
        "detail": event.detail,
    }


def _emit_openclaw_failure(
    console,  # noqa: ANN001
    quiet: bool,
    home: WorthlessHome,
    alias_count: int,
    detail: str,
) -> None:
    """Print [FAIL] block + write partial sentinel for the unexpected-raise path.

    Used when ``apply_lock`` raises despite contracting not to. Mirrors the
    in-line FAIL block above — we don't know exactly what failed, but the
    user is in a genuinely broken state and must be told loudly.
    """
    if not quiet:
        console.print_failure("[FAIL] OpenClaw integration did NOT complete.")
        console.print_warning("   Your .env is locked, but OpenClaw is still calling the")
        console.print_warning("   provider directly with the unsplit key.")
        console.print_warning(f"   detail: {detail}")
        console.print_warning("")
        console.print_warning("   Fix:       worthless doctor")
        console.print_warning("   Roll back: worthless unlock")
    _write_lock_sentinel(
        home,
        status="partial",
        openclaw="failed",
        alias_count=alias_count,
        events=({"code": "openclaw.unexpected_raise", "level": "error", "detail": detail},),
    )


def _write_lock_sentinel(
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


def _lock_keys(
    env_path: Path,
    home: WorthlessHome,
    provider_override: str | None = None,
    token_budget_daily: int | None = None,
    quiet: bool = False,
    keys_only: bool = False,
) -> int:
    """Transactional multi-key lock.

    Pass-1 does every DB write and builds a ``_PlannedUpdate`` list. A single
    ``rewrite_env_keys`` call then updates ``.env`` atomically, with a verify
    hook that reconstructs every shard before the rename commits. On any
    failure the DB writes are unwound so the system is consistent either way.
    """
    console = get_console()

    if not env_path.exists():
        raise WorthlessError(ErrorCode.ENV_NOT_FOUND, f"File not found: {env_path}")
    if env_path.is_symlink():
        raise WorthlessError(ErrorCode.ENV_NOT_FOUND, f"Refusing to follow symlink: {env_path}")

    if not quiet:
        console.print_hint(f"Scanning {env_path} for API keys...")

    async def _lock_async() -> tuple[int, bool]:
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()

        env_str = str(env_path.resolve())
        all_enrollments = await repo.list_enrollments()
        enrolled_locations = build_enrolled_locations(all_enrollments)

        scanned = scan_env_keys(env_path, enrolled_locations=enrolled_locations)
        if not scanned:
            console.print_warning("No unprotected API keys found.")
            return 0, False

        candidates = [
            (var_name, value, provider_override or detected_provider)
            for var_name, value, detected_provider in scanned
        ]
        existing_env_keys = {var_name for var_name, _, _ in scanned}

        planned: list[_PlannedUpdate] = []
        try:
            if not quiet:
                console.print_hint(f"  Protecting {len(candidates)} key(s)...")
            await _pass1_db_writes(repo, candidates, env_str, token_budget_daily, planned)
            if not planned:
                return 0, False
            _batch_rewrite(env_path, planned, keys_only, existing_env_keys)
            # Phase 2.b: OpenClaw magic. Per L1 in
            # engineering/research/openclaw-WOR-431-phase-2-spec.md, this
            # NEVER rolls back lock-core success. Per L2 (revised 2026-05-08
            # by the verification gauntlet): detected+failed returns
            # partial_failure=True so the caller can raise typer.Exit(73)
            # AFTER lock-core's .env/DB writes are fully committed.
            partial_failure = _apply_openclaw(planned, console, quiet, home)
            return len(planned), partial_failure
        except Exception:
            if planned:
                unwind_errors = await _compensating_unwind(repo, planned)
                if unwind_errors:
                    console.print_warning(
                        f"Database may contain {len(unwind_errors)} stale row(s); "
                        "run `worthless unlock --all` to reconcile."
                    )
            raise
        finally:
            for p in planned:
                p.zero()

    count, partial_failure = asyncio.run(_lock_async())

    if count and env_path.exists():
        current = env_path.stat().st_mode
        if current & (stat.S_IRWXG | stat.S_IRWXO):
            env_path.chmod(current & ~(stat.S_IRWXG | stat.S_IRWXO))

    if not quiet:
        if count:
            console.print_success(f"[OK] Protected {count} key(s).")
            console.print_hint(
                "Next: run `worthless wrap <command>` or `worthless up` for daemon mode"
            )
        else:
            console.print_warning("No unprotected API keys found.")

    # Trust-fix (2026-05-08 verification gauntlet): when OpenClaw was
    # detected on this host AND the integration stage failed, the user is
    # in a false-invariant state — .env is locked, but their agent traffic
    # is not gated. Surface this LOUDLY by exiting non-zero AFTER the
    # lock-core writes have committed (L1 binding contract preserved).
    # Exit code 73 = EX_CANTCREAT (POSIX), distinguishable from 1.
    # The [FAIL] block is already printed by _apply_openclaw.
    if partial_failure:
        raise typer.Exit(code=73)

    return count


def _enroll_single(
    alias: str,
    key: str,
    provider: str,
    home: WorthlessHome,
) -> None:
    """Enroll a single key (no .env scanning).

    Write order: DB first — matching _lock_keys pattern.
    Compensation on failure: clean up the DB row.
    """
    if not _ALIAS_RE.match(alias):
        raise WorthlessError(ErrorCode.SCAN_ERROR, f"Invalid alias: {alias!r}")

    try:
        prefix = detect_prefix(key, provider)
    except ValueError:
        prefix = ""

    sr = split_key_fp(key, prefix, provider)

    async def _enroll_async():
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()

        existing = await repo.fetch_encrypted(alias)
        if existing is not None:
            raise WorthlessError(
                ErrorCode.SCAN_ERROR,
                f"Alias {alias!r} is already enrolled",
            )

        stored = StoredShard(
            shard_b=sr.shard_b,
            commitment=sr.commitment,
            nonce=sr.nonce,
            provider=provider,
        )
        await repo.store_enrolled(
            alias,
            stored,
            var_name=alias,
            env_path=None,
            prefix=sr.prefix,
            charset=sr.charset,
        )

    try:
        asyncio.run(_enroll_async())
    except WorthlessError:
        raise
    except Exception as exc:
        raise WorthlessError(
            ErrorCode.SHARD_STORAGE_FAILED,
            sanitize_exception(exc, generic="failed to enroll key"),
        ) from exc
    finally:
        sr.zero()

    console = get_console()
    console.print_success(f"Enrolled {alias} ({provider}).")


def register_lock_commands(app: typer.Typer) -> None:
    """Register lock and enroll commands on the Typer app."""

    @app.command()
    @error_boundary
    def lock(
        env: Path = typer.Option(Path(".env"), "--env", "-e", help="Path to .env file"),
        provider: str | None = typer.Option(
            None, "--provider", "-p", help="Override provider auto-detection"
        ),
        token_budget_daily: int | None = typer.Option(
            None, "--token-budget-daily", help="Daily token budget limit"
        ),
        keys_only: bool = typer.Option(
            False, "--keys-only", help="Only rewrite API keys (skip BASE_URL)"
        ),
    ) -> None:
        """Protect API keys in a .env file."""
        home = get_home()
        with acquire_lock(home):
            _lock_keys(
                env,
                home,
                provider_override=provider,
                token_budget_daily=token_budget_daily,
                keys_only=keys_only,
            )

    @app.command()
    @error_boundary
    def enroll(
        alias: str = typer.Option(..., "--alias", "-a", help="Key alias"),
        key: str | None = typer.Option(
            None,
            "--key",
            "-k",
            help="API key (use --key-stdin instead to avoid shell history)",
        ),
        key_stdin: bool = typer.Option(False, "--key-stdin", help="Read API key from stdin"),
        provider: str = typer.Option(..., "--provider", "-p", help="Provider name"),
    ) -> None:
        """Enroll a single API key (scripting/CI primitive)."""
        home = get_home()

        if key_stdin:
            actual_key = sys.stdin.readline().strip()
            if not actual_key:
                raise WorthlessError(ErrorCode.KEY_NOT_FOUND, "No key provided on stdin")
        elif key:
            actual_key = key
        else:
            raise WorthlessError(ErrorCode.KEY_NOT_FOUND, "Provide --key or --key-stdin")

        with acquire_lock(home):
            _enroll_single(alias, actual_key, provider, home)
