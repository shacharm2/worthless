"""Lock command -- scan .env, split keys (format-preserving), store shards."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import stat
import sys
from pathlib import Path

import typer

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, get_home
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import (
    add_or_rewrite_env_key,
    build_enrolled_locations,
    rewrite_env_key,
    scan_env_keys,
)
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary, sanitize_exception
from worthless.cli.key_patterns import detect_prefix
from worthless.cli.commands.up import _resolve_port
from worthless.cli.commands.wrap import _PROVIDER_ENV_MAP
from worthless.crypto.reconstruction import (
    _verify_commitment,  # noqa: PLC2701 — intentional internal use for re-lock guard
)
from worthless.crypto.splitter import (
    derive_shard_a_fp,
    split_key_fp,
)
from worthless.crypto.types import zero_buf
from worthless.exceptions import ShardTamperedError
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


def _lock_keys(
    env_path: Path,
    home: WorthlessHome,
    provider_override: str | None = None,
    token_budget_daily: int | None = None,
    quiet: bool = False,
    keys_only: bool = False,
) -> int:
    """Core lock logic. Returns count of keys protected.

    When *quiet* is True, suppress progress and summary output.
    The caller (e.g. the default command pipeline) controls its own
    output instead.

    When *keys_only* is True, only rewrite API key values in .env
    (skip BASE_URL injection).
    """
    console = get_console()

    if not env_path.exists():
        raise WorthlessError(ErrorCode.ENV_NOT_FOUND, f"File not found: {env_path}")

    if env_path.is_symlink():
        raise WorthlessError(
            ErrorCode.ENV_NOT_FOUND,
            f"Refusing to follow symlink: {env_path}",
        )

    if not quiet:
        console.print_hint(f"Scanning {env_path} for API keys...")

    async def _lock_async() -> int:
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()

        env_str = str(env_path.resolve())
        all_enrollments = await repo.list_enrollments()
        enrolled_locations = build_enrolled_locations(all_enrollments)

        keys = scan_env_keys(env_path, enrolled_locations=enrolled_locations)
        if not keys:
            console.print_warning("No unprotected API keys found.")
            return 0

        total = len(keys)
        count = 0

        for i, (var_name, value, detected_provider) in enumerate(keys, 1):
            if not quiet:
                console.print_hint(f"  [{i}/{total}] Protecting {var_name}...")
            provider = provider_override or detected_provider

            # Only enroll providers that wrap can redirect
            if provider not in _SUPPORTED_PROVIDERS:
                console.print_warning(
                    f"Skipping {var_name}: provider {provider!r} "
                    "not yet supported for proxy redirect"
                )
                continue

            alias = _make_alias(provider, value)

            # Re-lock guard: alias already in DB (same real key from another
            # var/env). Derive the shard-A that pairs with the stored shard-B
            # (modular inverse), verify the commitment, then rewrite this .env.
            # Silent no-op here would leave the real key plaintext on disk.
            db_shard = await repo.fetch_encrypted(alias)
            if db_shard is not None:
                if not db_shard.prefix or not db_shard.charset:
                    raise WorthlessError(
                        ErrorCode.SHARD_STORAGE_FAILED,
                        f"Alias {alias!r} predates format-preserving split "
                        "(no prefix/charset stored). Run `worthless unlock --all` "
                        "then re-lock this .env.",
                    )
                stored_decrypted = repo.decrypt_shard(db_shard)
                verify_payload = bytearray(value.encode("utf-8"))
                derived_shard_a: bytearray | None = None
                # Whole-file snapshot: the re-lock branch makes up to two
                # .env writes (key + BASE_URL) before the DB enrollment. A
                # failure between them would leave a half-rewritten .env
                # (e.g. real key restored but BASE_URL still pointing at
                # the proxy). Snapshot once, restore whole file on any fail.
                original_env_content: str | None = None
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
                            f"Alias {alias!r} exists but the provided {var_name} does "
                            "not match the originally-locked key (commitment mismatch).",
                        ) from exc

                    derived_shard_a = derive_shard_a_fp(
                        value,
                        stored_decrypted.shard_b,
                        db_shard.prefix,
                        db_shard.charset,
                    )
                    original_env_content = env_path.read_text()
                    rewrite_env_key(env_path, var_name, derived_shard_a.decode("utf-8"))

                    if not keys_only:
                        base_url_var = _PROVIDER_ENV_MAP.get(provider)
                        if base_url_var:
                            add_or_rewrite_env_key(env_path, base_url_var, _proxy_base_url(alias))

                    await repo.add_enrollment(
                        alias,
                        var_name=var_name,
                        env_path=env_str,
                    )
                    count += 1
                except Exception as exc:
                    if original_env_content is not None:
                        try:
                            env_path.write_text(original_env_content)
                        except Exception:
                            logger.debug("Failed to restore .env for %s", var_name, exc_info=True)
                    if isinstance(exc, WorthlessError):
                        raise
                    raise WorthlessError(
                        ErrorCode.SHARD_STORAGE_FAILED,
                        sanitize_exception(exc, generic="failed to re-lock key"),
                    ) from exc
                finally:
                    zero_buf(verify_payload)
                    if derived_shard_a is not None:
                        zero_buf(derived_shard_a)
                    stored_decrypted.zero()
                continue

            try:
                prefix = detect_prefix(value, provider)
            except ValueError:
                prefix = ""

            sr = split_key_fp(value, prefix, provider)
            db_written = False
            env_rewritten = False
            try:
                stored = StoredShard(
                    shard_b=sr.shard_b,
                    commitment=sr.commitment,
                    nonce=sr.nonce,
                    provider=provider,
                )
                # DB first -- atomic commit point
                await repo.store_enrolled(
                    alias,
                    stored,
                    var_name=var_name,
                    env_path=env_str,
                    token_budget_daily=token_budget_daily,
                    prefix=sr.prefix,
                    charset=sr.charset,
                )
                db_written = True

                # Rewrite .env: API_KEY = shard-A (format-preserving)
                shard_a_str = sr.shard_a.decode("utf-8")
                rewrite_env_key(env_path, var_name, shard_a_str)
                env_rewritten = True

                # Write BASE_URL unless --keys-only
                if not keys_only:
                    base_url_var = _PROVIDER_ENV_MAP.get(provider)
                    if base_url_var:
                        url = _proxy_base_url(alias)
                        add_or_rewrite_env_key(env_path, base_url_var, url)

                count += 1
            except Exception as exc:
                # Restore .env to original value if we rewrote it
                if env_rewritten:
                    try:
                        rewrite_env_key(env_path, var_name, value)
                    except Exception:
                        logger.debug("Failed to restore .env for %s", var_name, exc_info=True)
                if db_written:
                    await repo.delete_enrollment(alias, env_str)
                    remaining = await repo.list_enrollments(alias)
                    if not remaining:
                        await repo.delete_enrolled(alias)
                if isinstance(exc, WorthlessError):
                    raise
                raise WorthlessError(
                    ErrorCode.SHARD_STORAGE_FAILED,
                    sanitize_exception(exc, generic="failed to protect key"),
                ) from exc
            finally:
                sr.zero()

        return count

    count = asyncio.run(_lock_async())

    # Tighten .env permissions — shard-A is secret material
    if count and env_path.exists():
        current = env_path.stat().st_mode
        if current & (stat.S_IRWXG | stat.S_IRWXO):
            env_path.chmod(current & ~(stat.S_IRWXG | stat.S_IRWXO))

    if not quiet:
        if count:
            console.print_success(f"{count} key(s) protected.")
            console.print_hint(
                "Next: run `worthless wrap <command>` or `worthless up` for daemon mode"
            )
        else:
            console.print_warning("No unprotected API keys found.")

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
        # store_enrolled is atomic (BEGIN IMMEDIATE + commit) — no
        # partial state to compensate on failure.
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
