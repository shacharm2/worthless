"""Lock command -- scan .env, split keys, store shards, rewrite with decoys."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path

import typer

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, get_home
from worthless.cli.console import get_console
from worthless.cli.decoy import make_decoy
from worthless.cli.dotenv_rewriter import rewrite_env_key, scan_env_keys, shannon_entropy
from worthless.cli.errors import ErrorCode, WorthlessError, sanitize_exception
from worthless.cli.key_patterns import ENTROPY_THRESHOLD, detect_prefix
from worthless.cli.commands.wrap import _PROVIDER_ENV_MAP
from worthless.crypto.splitter import split_key
from worthless.storage.repository import ShardRepository, StoredShard

_SUPPORTED_PROVIDERS = frozenset(_PROVIDER_ENV_MAP.keys())
_ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


# Pattern matching the literal "WRTLS" marker in old-format decoys.
_OLD_DECOY_MARKER = "WRTLS"


def _make_alias(provider: str, api_key: str) -> str:
    """Deterministic alias: provider + first 8 hex chars of sha256(key)."""
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:8]  # nosec B303 -- non-cryptographic fingerprint
    return f"{provider}-{digest}"


async def _migrate_old_decoys(
    env_path: Path,
    repo: ShardRepository,
) -> int:
    """Upgrade old WRTLS-marker decoys to high-entropy CSPRNG format.

    Old ``_make_decoy()`` generated values like
    ``sk-proj-a1b2c3d4WRTLSWRTLSWRTLS...`` — low entropy, contains
    the literal ``WRTLS`` substring.  These are invisible to
    ``scan_env_keys()`` because their Shannon entropy falls below
    ``ENTROPY_THRESHOLD``.

    This function reads the ``.env`` file directly, identifies old
    decoys by the ``WRTLS`` marker + low entropy, looks up the
    matching enrollment, and replaces them with new format-correct
    decoys.

    Returns the number of decoys migrated.
    """
    console = get_console()
    text = env_path.read_text()
    env_str = str(env_path.resolve())
    migrated = 0

    for line in text.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if "=" not in line_stripped:
            continue

        var_name, _, raw_value = line_stripped.partition("=")
        var_name = var_name.strip()
        value = raw_value.strip().strip("\"'")

        # Detect old decoys: must contain "WRTLS" AND have low entropy
        if _OLD_DECOY_MARKER not in value:
            continue
        if shannon_entropy(value) >= ENTROPY_THRESHOLD:
            continue

        # Look up enrollment for this var_name + env_path
        enrollment = await repo.find_enrollment_by_location(var_name, env_str)
        if enrollment is None:
            continue

        # Only migrate if decoy_hash is not yet set (old decoys were
        # created before the hash registry existed)
        if enrollment.decoy_hash is not None:
            continue

        # Determine provider from the shard record
        shard = await repo.fetch_encrypted(enrollment.key_alias)
        if shard is None:
            continue

        provider = shard.provider
        try:
            prefix = detect_prefix(value, provider)
        except ValueError:
            prefix = ""

        new_decoy = make_decoy(provider, prefix)
        # DB first: if crash after DB write but before file write, the old
        # WRTLS decoy stays in .env (low entropy -> still filtered by scan)
        # and migration retries are harmless (decoy_hash set -> skipped).
        await repo.set_decoy_hash(enrollment.key_alias, env_str, new_decoy)
        rewrite_env_key(env_path, var_name, new_decoy)
        migrated += 1
        console.print_success(f"Migrated old decoy for {var_name}")

    return migrated


def _lock_keys(
    env_path: Path,
    home: WorthlessHome,
    provider_override: str | None = None,
) -> int:
    """Core lock logic. Returns count of keys protected."""
    console = get_console()

    if not env_path.exists():
        raise WorthlessError(ErrorCode.ENV_NOT_FOUND, f"File not found: {env_path}")

    if env_path.is_symlink():
        raise WorthlessError(
            ErrorCode.ENV_NOT_FOUND,
            f"Refusing to follow symlink: {env_path}",
        )

    async def _lock_async() -> int:
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()

        # Migrate old WRTLS-marker decoys before scanning
        await _migrate_old_decoys(env_path, repo)

        # Pre-fetch decoy hashes for sync predicate injection
        decoy_hashes = await repo.all_decoy_hashes()

        def _is_decoy(value: str) -> bool:
            return repo._compute_decoy_hash(value) in decoy_hashes

        keys = scan_env_keys(env_path, is_decoy=_is_decoy)
        if not keys:
            console.print_warning("No unprotected API keys found.")
            return 0

        count = 0

        for var_name, value, detected_provider in keys:
            provider = provider_override or detected_provider

            # Only enroll providers that wrap can redirect
            if provider not in _SUPPORTED_PROVIDERS:
                console.print_warning(
                    f"Skipping {var_name}: provider {provider!r} "
                    "not yet supported for proxy redirect"
                )
                continue

            alias = _make_alias(provider, value)

            shard_a_path = home.shard_a_dir / alias
            if shard_a_path.exists():
                # Shard file already exists.  Check if the DB row exists too
                # (orphan shard_a = file without DB row -- warn and skip).
                db_shard = await repo.fetch_encrypted(alias)
                if db_shard is None:
                    console.print_warning(
                        f"Skipping {var_name} (orphan shard_a for {alias}, no DB record)"
                    )
                    continue

                # Shard fully enrolled -- still need to:
                # 1. Create enrollment for THIS var_name/env_path
                # 2. Rewrite THIS .env line with a decoy
                shard_a_path.read_bytes()  # validate file is readable
                await repo.add_enrollment(
                    alias,
                    var_name=var_name,
                    env_path=str(env_path.resolve()),
                )
                try:
                    prefix = detect_prefix(value, provider)
                except ValueError:
                    prefix = ""
                decoy = make_decoy(provider, prefix)
                rewrite_env_key(env_path, var_name, decoy)
                env_str = str(env_path.resolve())
                await repo.set_decoy_hash(alias, env_str, decoy)
                count += 1
                continue

            sr = split_key(value.encode())
            shard_a_written = False
            db_written = False
            try:
                try:
                    prefix = detect_prefix(value, provider)
                except ValueError:
                    prefix = ""

                stored = StoredShard(
                    shard_b=bytearray(sr.shard_b),
                    commitment=bytearray(sr.commitment),
                    nonce=bytearray(sr.nonce),
                    provider=provider,
                )
                # DB first -- atomic commit point
                await repo.store_enrolled(
                    alias,
                    stored,
                    var_name=var_name,
                    env_path=str(env_path.resolve()),
                )
                db_written = True

                # shard_a file second
                fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, bytes(sr.shard_a))  # nosemgrep: sr01-key-material-not-bytearray
                finally:
                    os.close(fd)
                shard_a_written = True

                # .env rewrite last
                decoy = make_decoy(provider, prefix)
                rewrite_env_key(env_path, var_name, decoy)
                await repo.set_decoy_hash(alias, str(env_path.resolve()), decoy)

                count += 1
            except Exception as exc:
                # Compensate: clean up partial state
                if shard_a_written:
                    shard_a_path.unlink(missing_ok=True)
                if db_written:
                    # Only delete THIS specific enrollment -- not the shard
                    # or other enrollments (CASCADE would destroy them all).
                    env_str = str(env_path.resolve())
                    await repo.delete_enrollment(alias, env_str)
                    remaining = await repo.list_enrollments(alias)
                    if not remaining:
                        # No other enrollments -- safe to remove shard too
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

    if count:
        console.print_success(f"{count} key(s) protected.")
    else:
        console.print_warning("No unprotected API keys found.")

    return count


def _enroll_single(
    alias: str,
    key: str,
    provider: str,
    home: WorthlessHome,
) -> None:
    """Enroll a single key (no .env scanning)."""
    if not _ALIAS_RE.match(alias):
        raise WorthlessError(ErrorCode.SCAN_ERROR, f"Invalid alias: {alias!r}")

    async def _enroll_async():
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()
        stored = StoredShard(
            shard_b=bytearray(sr.shard_b),
            commitment=bytearray(sr.commitment),
            nonce=bytearray(sr.nonce),
            provider=provider,
        )
        await repo.store_enrolled(
            alias,
            stored,
            var_name=alias,
            env_path=None,
        )

    sr = split_key(key.encode())
    try:
        shard_a_path = home.shard_a_dir / alias
        fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, bytes(sr.shard_a))  # nosemgrep: sr01-key-material-not-bytearray
        finally:
            os.close(fd)

        asyncio.run(_enroll_async())
    finally:
        sr.zero()

    console = get_console()
    console.print_success(f"Enrolled {alias} ({provider}).")


def register_lock_commands(app: typer.Typer) -> None:
    """Register lock and enroll commands on the Typer app."""

    @app.command()
    def lock(
        env: Path = typer.Option(Path(".env"), "--env", "-e", help="Path to .env file"),
        provider: str | None = typer.Option(
            None, "--provider", "-p", help="Override provider auto-detection"
        ),
    ) -> None:
        """Protect API keys in a .env file."""
        console = get_console()
        home = get_home()
        try:
            with acquire_lock(home):
                _lock_keys(env, home, provider_override=provider)
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            console.print_error(WorthlessError(ErrorCode.UNKNOWN, sanitize_exception(exc)))
            raise typer.Exit(code=1) from exc

    @app.command()
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
        import sys

        console = get_console()
        home = get_home()

        if key_stdin:
            actual_key = sys.stdin.readline().strip()
            if not actual_key:
                console.print_error(
                    WorthlessError(ErrorCode.KEY_NOT_FOUND, "No key provided on stdin")
                )
                raise typer.Exit(code=1)
        elif key:
            actual_key = key
        else:
            console.print_error(
                WorthlessError(ErrorCode.KEY_NOT_FOUND, "Provide --key or --key-stdin")
            )
            raise typer.Exit(code=1)

        try:
            with acquire_lock(home):
                _enroll_single(alias, actual_key, provider, home)
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            console.print_error(WorthlessError(ErrorCode.UNKNOWN, sanitize_exception(exc)))
            raise typer.Exit(code=1) from exc
