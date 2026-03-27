"""Lock command — scan .env, split keys, store shards, rewrite with decoys."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, ensure_home, get_home
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import rewrite_env_key, scan_env_keys
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.key_patterns import detect_prefix, detect_provider
from worthless.cli.commands.wrap import _PROVIDER_ENV_MAP
from worthless.crypto.splitter import split_key

_SUPPORTED_PROVIDERS = frozenset(_PROVIDER_ENV_MAP.keys())
from worthless.crypto.types import _zero_buf
from worthless.storage.repository import ShardRepository, StoredShard


def _make_alias(provider: str, api_key: str) -> str:
    """Deterministic alias: provider + first 8 hex chars of sha256(key)."""
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:8]
    return f"{provider}-{digest}"


def _make_decoy(original: str, prefix: str, shard_a: bytes) -> str:
    """Build a prefix-preserving decoy of the same length as *original*.

    The decoy uses a low-entropy repeating pattern so that scan_env_keys()
    filters it out on re-scan (Shannon entropy < 4.5 threshold), making
    lock idempotent.  The 8-char hex digest gives the decoy a unique look
    while keeping overall entropy low via the repeating 'WRTLS' filler.
    """
    suffix_len = len(original) - len(prefix)
    # Use 8 hex chars from shard_a hash for some uniqueness, then fill with
    # low-entropy repeating pattern to stay below the entropy threshold.
    tag = hashlib.sha256(shard_a).hexdigest()[:8]
    filler = "WRTLS" * ((suffix_len // 5) + 2)
    raw = tag + filler
    return prefix + raw[:suffix_len]


def _lock_keys(
    env_path: Path,
    home: WorthlessHome,
    provider_override: str | None = None,
) -> int:
    """Core lock logic. Returns count of keys protected."""
    console = get_console()

    if not env_path.exists():
        raise WorthlessError(ErrorCode.ENV_NOT_FOUND, f"File not found: {env_path}")

    keys = scan_env_keys(env_path)
    if not keys:
        console.print_warning("No unprotected API keys found.")
        return 0

    async def _lock_async() -> int:
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()
        count = 0

        for var_name, value, detected_provider in keys:
            provider = provider_override or detected_provider

            # Only enroll providers that wrap can redirect
            if provider not in _SUPPORTED_PROVIDERS:
                console.print_warning(
                    f"Skipping {var_name}: provider {provider!r} not yet supported for proxy redirect"
                )
                continue

            alias = _make_alias(provider, value)

            shard_a_path = home.shard_a_dir / alias
            if shard_a_path.exists():
                console.print_warning(f"Skipping {var_name} (already enrolled as {alias})")
                continue

            sr = split_key(value.encode())
            try:
                try:
                    prefix = detect_prefix(value, provider)
                except ValueError:
                    prefix = ""

                fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, bytes(sr.shard_a))
                finally:
                    os.close(fd)

                stored = StoredShard(
                    shard_b=bytearray(sr.shard_b),
                    commitment=bytearray(sr.commitment),
                    nonce=bytearray(sr.nonce),
                    provider=provider,
                )
                await repo.store(alias, stored)

                meta_path = home.shard_a_dir / f"{alias}.meta"
                meta_fd = os.open(str(meta_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    os.write(meta_fd, json.dumps({
                        "var_name": var_name,
                        "env_path": str(env_path.resolve()),
                    }).encode())
                finally:
                    os.close(meta_fd)

                decoy = _make_decoy(value, prefix, bytes(sr.shard_a))
                rewrite_env_key(env_path, var_name, decoy)

                count += 1
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
    _ALIAS_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
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
        await repo.store(alias, stored)

    sr = split_key(key.encode())
    try:
        shard_a_path = home.shard_a_dir / alias
        fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, bytes(sr.shard_a))
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
        env: Path = typer.Option(
            Path(".env"), "--env", "-e", help="Path to .env file"
        ),
        provider: Optional[str] = typer.Option(
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

    @app.command()
    def enroll(
        alias: str = typer.Option(..., "--alias", "-a", help="Key alias"),
        key: Optional[str] = typer.Option(None, "--key", "-k", help="API key (use --key-stdin instead to avoid shell history)"),
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
                console.print_error(WorthlessError(ErrorCode.KEY_NOT_FOUND, "No key provided on stdin"))
                raise typer.Exit(code=1)
        elif key:
            actual_key = key
        else:
            console.print_error(WorthlessError(ErrorCode.KEY_NOT_FOUND, "Provide --key or --key-stdin"))
            raise typer.Exit(code=1)

        try:
            _enroll_single(alias, actual_key, provider, home)
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
