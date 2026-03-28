# Worthless CLI — Scenario Verification Prompt

> Paste this entire document into ANY LLM to get a complete security and correctness audit.
> All source code is embedded. You need nothing else.

## System Overview

**Worthless** is a split-key reverse proxy CLI that protects API keys at rest via XOR secret sharing.

**Commands**: lock, unlock, enroll, scan, status, wrap, up

**State stores**:
- `~/.worthless/shard_a/{alias}` — binary XOR shard files (0o600)
- `~/.worthless/worthless.db` — SQLite with shards table (encrypted shard_b) + enrollments table (var_name, env_path)
- `~/.worthless/fernet.key` — Fernet encryption key for shard_b at rest
- `.env` files — rewritten with prefix-preserving low-entropy decoys after lock

**Key invariants**:
- INV-1: For every shard_a file, a matching row exists in shards table (and vice versa)
- INV-2: For every enrollment row, the referenced key_alias exists in shards (FK constraint)
- INV-3: A locked .env contains only decoy values for enrolled keys
- INV-4: reconstruct_key(shard_a, shard_b, commitment, nonce) always recovers the original key

---

## Source Code

### `src/worthless/cli/commands/lock.py`

```python
"""Lock command — scan .env, split keys, store shards, rewrite with decoys."""

from __future__ import annotations

import asyncio
import base64
import hashlib
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
                # DB first — atomic commit point
                await repo.store_enrolled(
                    alias, stored,
                    var_name=var_name,
                    env_path=str(env_path.resolve()),
                )
                db_written = True

                # shard_a file second
                fd = os.open(str(shard_a_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, bytes(sr.shard_a))
                finally:
                    os.close(fd)
                shard_a_written = True

                # .env rewrite last
                decoy = _make_decoy(value, prefix, bytes(sr.shard_a))
                rewrite_env_key(env_path, var_name, decoy)

                count += 1
            except Exception:
                # Compensate: clean up partial state
                if shard_a_written:
                    shard_a_path.unlink(missing_ok=True)
                if db_written:
                    await repo.delete_enrolled(alias)
                raise
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
        await repo.store_enrolled(alias, stored, var_name=alias, env_path=None)

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

```

### `src/worthless/cli/commands/unlock.py`

```python
"""Unlock command — reconstruct keys from shards, restore .env, clean up."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home, get_home
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import rewrite_env_key
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.crypto.splitter import reconstruct_key
from worthless.crypto.types import _zero_buf
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
        raise WorthlessError(ErrorCode.KEY_NOT_FOUND, f"Shard A not found for alias: {alias}")

    shard_a = bytearray(shard_a_path.read_bytes())

    stored = await repo.retrieve(alias)
    if stored is None:
        _zero_buf(shard_a)
        raise WorthlessError(ErrorCode.KEY_NOT_FOUND, f"Shard B not found in DB for alias: {alias}")

    # Read var_name from DB enrollment — check for ambiguity
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

    try:
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

            # Delete this specific enrollment
            env_str = str(env_path.resolve()) if env_path else None
            remaining = await repo.list_enrollments(alias)
            if env_str:
                await repo.delete_enrollment(alias, env_str)
                remaining = [e for e in remaining if e.env_path != env_str]
            else:
                # No env specified — delete all enrollments for this alias
                remaining = []

            # Only delete shard + shard_a file when no enrollments remain
            if not remaining:
                shard_a_path.unlink(missing_ok=True)
                await repo.delete_enrolled(alias)

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
            asyncio.run(_unlock_async())
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc

```

### `src/worthless/cli/commands/wrap.py`

```python
"""wrap command — ephemeral proxy + child process lifecycle.

``worthless wrap python main.py`` starts a transparent proxy on a random port,
injects ``{PROVIDER}_BASE_URL`` env vars so API calls route through it, runs
the child, and cleans up when the child exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home, get_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.process import (
    create_liveness_pipe,
    disable_core_dumps,
    forward_signals,
    poll_health,
    spawn_proxy,
)

# Provider -> env var mapping for BASE_URL injection
_PROVIDER_ENV_MAP: dict[str, str] = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
}


def _list_enrolled_providers(home: WorthlessHome) -> list[str]:
    """List providers that have enrolled keys (shard_a files exist).

    Extracts provider name from alias format: ``provider-hash8``.
    """
    providers: set[str] = set()
    shard_dir = home.shard_a_dir
    if not shard_dir.exists():
        return []

    for entry in shard_dir.iterdir():
        # Extract provider from alias (format: "provider-hash8")
        name = entry.name
        if "-" in name:
            provider = name.rsplit("-", 1)[0]
            providers.add(provider)

    return sorted(providers)


def _build_child_env(
    port: int,
    providers: list[str],
) -> dict[str, str]:
    """Build environment for the child process.

    Inherits the current env and adds {PROVIDER}_BASE_URL for each
    enrolled provider so SDK calls route through the proxy.
    """
    env = dict(os.environ)
    for provider in providers:
        env_var = _PROVIDER_ENV_MAP.get(provider)
        if env_var:
            env[env_var] = f"http://127.0.0.1:{port}"
    return env


def _run_child_and_wait(child: subprocess.Popen) -> int:
    """Wait for child to exit, return its exit code."""
    child.wait()
    return child.returncode


def _cleanup_proxy(
    proxy: subprocess.Popen,
    write_fd: int | None = None,
    timeout: float = 5.0,
) -> None:
    """Shut down the proxy process gracefully.

    1. Close write_fd (triggers EOF on liveness pipe -> proxy self-terminates)
    2. Terminate proxy
    3. Wait with timeout, SIGKILL if stuck
    """
    # Close liveness pipe write end
    if write_fd is not None:
        try:
            os.close(write_fd)
        except OSError:
            pass

    if proxy.poll() is not None:
        return  # Already dead

    try:
        proxy.terminate()
        proxy.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proxy.kill()
        proxy.wait(timeout=2)


def register_wrap_commands(app: typer.Typer) -> None:
    """Register the ``wrap`` command on the Typer app."""

    @app.command(
        context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
    )
    def wrap(
        ctx: typer.Context,
        command: list[str] = typer.Argument(
            ..., help="Command to run (e.g. python main.py)"
        ),
    ) -> None:
        """Start ephemeral proxy, inject env vars, run COMMAND, clean up."""
        console = get_console()

        # Load home, verify keys enrolled
        home = get_home()
        providers = _list_enrolled_providers(home)
        if not providers:
            console.print_error(
                WorthlessError(
                    ErrorCode.KEY_NOT_FOUND,
                    "No keys enrolled. Run 'worthless lock' first.",
                )
            )
            raise typer.Exit(code=1)

        # Suppress core dumps
        disable_core_dumps()

        # Create liveness pipe
        read_fd, write_fd = create_liveness_pipe()

        # Build proxy environment
        proxy_env = {
            "WORTHLESS_DB_PATH": str(home.db_path),
            "WORTHLESS_FERNET_KEY": home.fernet_key.decode(),
            "WORTHLESS_SHARD_A_DIR": str(home.shard_a_dir),
        }

        # Spawn proxy on random port
        try:
            proxy, port = spawn_proxy(
                env=proxy_env,
                port=0,
                liveness_fd=read_fd,
            )
        except Exception as exc:
            os.close(read_fd)
            os.close(write_fd)
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, f"Failed to start proxy: {exc}")
            )
            raise typer.Exit(code=1) from exc

        # Close read_fd in parent (proxy has it)
        os.close(read_fd)

        # Poll health
        healthy = poll_health(port, timeout=15.0)
        if not healthy:
            _cleanup_proxy(proxy, write_fd)
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, "Proxy failed to become healthy")
            )
            raise typer.Exit(code=1)

        # Build child env with BASE_URL injection
        full_command = list(command) + ctx.args
        child_env = _build_child_env(port, providers)

        # Spawn child
        try:
            child = subprocess.Popen(
                full_command,
                env=child_env,
                process_group=0,
            )
        except Exception as exc:
            _cleanup_proxy(proxy, write_fd)
            console.print_error(
                WorthlessError(ErrorCode.WRAP_CHILD_FAILED, f"Failed to start child: {exc}")
            )
            raise typer.Exit(code=1) from exc

        # Register signal forwarding
        forward_signals(proxy=proxy, child=child)

        # Monitor proxy in background — warn on crash but don't kill child
        import threading

        def _watch_proxy():
            proxy.wait()
            if child.poll() is None:
                # Proxy died while child still running
                sys.stderr.write(
                    "worthless: warning: proxy crashed mid-session, "
                    "child continues without protection\n"
                )
                sys.stderr.flush()

        watcher = threading.Thread(target=_watch_proxy, daemon=True)
        watcher.start()

        # Wait for child
        exit_code = _run_child_and_wait(child)

        # Clean up proxy
        _cleanup_proxy(proxy, write_fd)

        raise typer.Exit(code=exit_code)

```

### `src/worthless/cli/commands/up.py`

```python
"""up command — standalone proxy daemon/foreground.

``worthless up`` starts the proxy in foreground on port 8787.
``worthless up -d`` starts it in daemon mode (background).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home, get_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.process import (
    check_pid,
    cleanup_stale_pid,
    disable_core_dumps,
    forward_signals,
    poll_health,
    read_pid,
    spawn_proxy,
    write_pid,
)


def _pid_path(home: WorthlessHome) -> Path:
    """Return the PID file path."""
    return home.base_dir / "proxy.pid"


def _resolve_port(port_arg: int | None) -> int:
    """Resolve port from argument, env var, or default.

    Priority: explicit arg > WORTHLESS_PORT env > 8787 default.
    """
    if port_arg is not None:
        return port_arg
    env_port = os.environ.get("WORTHLESS_PORT")
    if env_port:
        return int(env_port)
    return 8787


def register_up_commands(app: typer.Typer) -> None:
    """Register the ``up`` command on the Typer app."""

    @app.command()
    def up(
        port: Optional[int] = typer.Option(
            None, "--port", "-p", help="Port to bind (default: 8787 or WORTHLESS_PORT)"
        ),
        daemon: bool = typer.Option(
            False, "--daemon", "-d", help="Run in background (daemon mode)"
        ),
    ) -> None:
        """Start the proxy server (foreground or daemon)."""
        console = get_console()
        home = get_home()

        actual_port = _resolve_port(port)

        # Check PID file for existing proxy
        pid_file = _pid_path(home)
        if pid_file.exists():
            info = read_pid(pid_file)
            if info is not None:
                existing_pid, existing_port = info
                if check_pid(existing_pid):
                    console.print_error(
                        WorthlessError(
                            ErrorCode.PORT_IN_USE,
                            f"Proxy already running (PID {existing_pid} on port {existing_port}). "
                            f"Stop it first or use a different port.",
                        )
                    )
                    raise typer.Exit(code=1)
                else:
                    # Stale PID file — reclaim
                    cleanup_stale_pid(pid_file)
                    console.print_warning(f"Reclaimed stale PID file (was PID {existing_pid})")

        # Disable core dumps
        disable_core_dumps()

        # Build proxy env
        proxy_env = {
            "WORTHLESS_DB_PATH": str(home.db_path),
            "WORTHLESS_FERNET_KEY": home.fernet_key.decode(),
            "WORTHLESS_SHARD_A_DIR": str(home.shard_a_dir),
        }

        if daemon:
            _start_daemon(proxy_env, actual_port, pid_file, console)
        else:
            _start_foreground(proxy_env, actual_port, pid_file, console)

    def _start_daemon(
        proxy_env: dict[str, str],
        port: int,
        pid_file: Path,
        console,
    ) -> None:
        """Start proxy in daemon mode (setsid, write PID, detach)."""
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "worthless.proxy.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]

        # Pass Fernet key via fd, not env var
        fernet_key = proxy_env.pop("WORTHLESS_FERNET_KEY", None)
        fernet_fd: int | None = None
        if fernet_key:
            r_fd, w_fd = os.pipe()
            os.write(w_fd, fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
            os.close(w_fd)
            fernet_fd = r_fd

        full_env = {
            **os.environ,
            **proxy_env,
            "WORTHLESS_ALLOW_INSECURE": proxy_env.get("WORTHLESS_ALLOW_INSECURE", "true"),
        }
        pass_fds: list[int] = []
        if fernet_fd is not None:
            full_env["WORTHLESS_FERNET_FD"] = str(fernet_fd)
            pass_fds.append(fernet_fd)

        # Start detached process
        proc = subprocess.Popen(
            cmd,
            env=full_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=tuple(pass_fds),
        )

        # Write PID file
        write_pid(pid_file, proc.pid, port)

        # Brief health check
        healthy = poll_health(port, timeout=10.0)
        if healthy:
            console.print_success(f"Proxy running on 127.0.0.1:{port} (PID {proc.pid})")
        else:
            console.print_warning(
                f"Proxy started (PID {proc.pid}) but health check timed out. "
                f"Check logs."
            )

    def _start_foreground(
        proxy_env: dict[str, str],
        port: int,
        pid_file: Path,
        console,
    ) -> None:
        """Start proxy in foreground mode (blocks until SIGINT/SIGTERM)."""
        try:
            proxy, actual_port = spawn_proxy(env=proxy_env, port=port)
        except Exception as exc:
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, f"Failed to start proxy: {exc}")
            )
            raise typer.Exit(code=1) from exc

        # Write PID file
        write_pid(pid_file, proxy.pid, actual_port)

        # Poll health
        healthy = poll_health(actual_port, timeout=15.0)
        if not healthy:
            proxy.terminate()
            proxy.wait(timeout=5)
            pid_file.unlink(missing_ok=True)
            console.print_error(
                WorthlessError(ErrorCode.PROXY_UNREACHABLE, "Proxy failed to become healthy")
            )
            raise typer.Exit(code=1)

        console.print_success(f"Proxy running on 127.0.0.1:{actual_port} (Ctrl+C to stop)")

        # Register signal handler that cleans up PID file and stops proxy
        def _cleanup(signum, frame):
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=2)
            pid_file.unlink(missing_ok=True)
            console.print_warning("Proxy stopped.")
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _cleanup)
        signal.signal(signal.SIGTERM, _cleanup)

        # Wait for proxy to exit (either by signal or crash)
        proxy.wait()
        pid_file.unlink(missing_ok=True)

```

### `src/worthless/cli/commands/scan.py`

```python
"""Scan command — detect exposed API keys with decoy awareness."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Literal, Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.scanner import ScanFinding, format_sarif, scan_files


def _find_git_dir() -> Path | None:
    """Find .git directory, checking GIT_DIR env var first."""
    env_git = os.environ.get("GIT_DIR")
    if env_git:
        p = Path(env_git)
        if p.is_dir():
            return p
        return None
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        git = parent / ".git"
        if git.is_dir():
            return git
    return None


def _collect_fast_paths(explicit_paths: list[Path]) -> list[Path]:
    """Fast mode: .env, .env.local, plus any explicit paths."""
    paths: list[Path] = []
    for name in [".env", ".env.local"]:
        p = Path(name)
        if p.exists():
            paths.append(p)
    paths.extend(explicit_paths)
    return paths


def _collect_deep_paths(explicit_paths: list[Path]) -> tuple[list[Path], Path | None]:
    """Deep mode: fast paths + config files in project root + env dump.

    Returns (paths, tmp_file) — caller must unlink tmp_file when done.
    """
    paths = _collect_fast_paths(explicit_paths)
    tmp_path: Path | None = None

    for pattern in ["*.yml", "*.yaml", "*.toml", "*.json"]:
        for p in Path(".").glob(pattern):
            if p.is_file() and p not in paths:
                paths.append(p)

    env_lines = [f"{k}={v}" for k, v in os.environ.items()]
    if env_lines:
        fd, tmp = tempfile.mkstemp(prefix="worthless-env-", suffix=".env")
        try:
            os.write(fd, "\n".join(env_lines).encode())
            os.close(fd)
            tmp_path = Path(tmp)
            paths.append(tmp_path)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass

    return paths, tmp_path


def _format_human(
    findings: list[ScanFinding],
    show_suffix: bool = False,
    is_tty: bool = True,
) -> str:
    """Format findings as human-readable text."""
    if not findings:
        return "No API keys found.\n"

    lines: list[str] = []
    unprotected_count = 0
    protected_count = 0
    file_cache: dict[str, str] = {}

    for f in findings:
        status = "PROTECTED" if f.is_protected else "UNPROTECTED"
        preview = f.value_preview
        if show_suffix and not f.is_protected:
            try:
                from worthless.cli.key_patterns import KEY_PATTERN

                if f.file not in file_cache:
                    file_cache[f.file] = Path(f.file).read_text(errors="replace")
                text = file_cache[f.file]
                for line in text.splitlines():
                    for match in KEY_PATTERN.finditer(line):
                        value = match.group(0)
                        if preview.startswith(value[:4]):
                            preview = f.value_preview + "..." + value[-4:]
                            break
            except Exception:
                pass

        var_part = f" ({f.var_name})" if f.var_name else ""
        lines.append(f"  {f.file}:{f.line}  {f.provider}{var_part}  {status}  {preview}")

        if f.is_protected:
            protected_count += 1
        else:
            unprotected_count += 1

    total = len(findings)
    lines.append("")
    lines.append(f"Found {total} keys: {protected_count} protected, {unprotected_count} unprotected")

    if unprotected_count > 0:
        if is_tty:
            lines.append("Run: worthless lock")
        else:
            lines.append("See: docs.worthless.dev/ci-setup")

    return "\n".join(lines) + "\n"


def _format_json_findings(findings: list[ScanFinding]) -> str:
    """Format findings as JSON array."""
    items = []
    for f in findings:
        items.append({
            "file": f.file,
            "line": f.line,
            "var_name": f.var_name,
            "provider": f.provider,
            "is_protected": f.is_protected,
            "value_preview": f.value_preview,
        })
    return json.dumps(items, indent=2) + "\n"


def _install_hook() -> None:
    """Write or append worthless scan to .git/hooks/pre-commit."""
    git_dir = _find_git_dir()
    if git_dir is None:
        raise WorthlessError(ErrorCode.SCAN_ERROR, "No .git directory found")

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"

    marker = "# worthless-scan-hook"
    snippet = f'\n{marker}\nworthless scan --pre-commit "$@"\n'

    if hook_path.exists():
        content = hook_path.read_text()
        if marker in content:
            return  # already installed
        hook_path.write_text(content + snippet)
    else:
        hook_path.write_text(f"#!/bin/sh\n{snippet}")

    # Make executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def register_scan_commands(app: typer.Typer) -> None:
    """Register the scan command on the Typer app."""

    @app.command()
    def scan(
        paths: Optional[list[Path]] = typer.Argument(None, help="Files to scan"),
        deep: bool = typer.Option(False, "--deep", help="Extended scan (env vars, config files)"),
        pre_commit: bool = typer.Option(False, "--pre-commit", help="Pre-commit hook mode"),
        format_: str = typer.Option("text", "--format", "-f", help="Output format: text, sarif, json", show_choices=True),
        show_suffix: bool = typer.Option(False, "--show-suffix", help="Show last 4 chars of keys"),
        install_hook: bool = typer.Option(False, "--install-hook", help="Install git pre-commit hook"),
        json_output: bool = typer.Option(False, "--json", help="Output JSON (alias for --format json)"),
    ) -> None:
        """Detect exposed API keys in files and environment."""
        console = get_console()

        # Handle --install-hook
        if install_hook:
            try:
                _install_hook()
                console.print_success("Pre-commit hook installed.")
            except WorthlessError as exc:
                console.print_error(exc)
                raise typer.Exit(code=2) from exc
            raise typer.Exit(code=0)

        # Resolve format
        fmt = format_
        if json_output:
            fmt = "json"
        if fmt not in ("text", "sarif", "json"):
            console.print_error(
                WorthlessError(ErrorCode.SCAN_ERROR, f"Unknown format: {fmt!r} (use text, sarif, or json)")
            )
            raise typer.Exit(code=2)

        tmp_file: Path | None = None
        try:
            # Collect files to scan
            explicit = list(paths) if paths else []
            if pre_commit:
                scan_paths = explicit
            elif deep:
                scan_paths, tmp_file = _collect_deep_paths(explicit)
            else:
                scan_paths = _collect_fast_paths(explicit)

            # Run scan
            findings = scan_files(scan_paths)

            # Count unprotected
            unprotected = [f for f in findings if not f.is_protected]

            # Output
            if fmt == "sarif":
                sarif = format_sarif(findings, "0.1.0")
                sys.stdout.write(json.dumps(sarif, indent=2) + "\n")
                sys.stdout.flush()
            elif fmt == "json":
                sys.stdout.write(_format_json_findings(findings))
                sys.stdout.flush()
            else:
                # Human-readable to stderr
                is_tty = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
                text = _format_human(findings, show_suffix=show_suffix, is_tty=is_tty)
                if not console.quiet:
                    sys.stderr.write(text)
                    sys.stderr.flush()

            # Exit code
            if unprotected:
                raise typer.Exit(code=1)
            raise typer.Exit(code=0)

        except typer.Exit:
            raise
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=2) from exc
        except Exception as exc:
            if not console.quiet:
                sys.stderr.write(f"Scan error: {type(exc).__name__}: {exc}\n")
            raise typer.Exit(code=2) from exc
        finally:
            if tmp_file is not None:
                tmp_file.unlink(missing_ok=True)

```

### `src/worthless/cli/commands/status.py`

```python
"""Status command — show enrolled keys and proxy health."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home, resolve_home
from worthless.cli.console import get_console
from worthless.cli.process import read_pid


def _list_enrolled_keys(home: WorthlessHome) -> list[dict[str, str]]:
    """List enrolled key aliases with providers from the DB."""
    import sqlite3

    keys: list[dict[str, str]] = []
    if not home.db_path.exists():
        return keys

    conn = sqlite3.connect(str(home.db_path))
    try:
        cursor = conn.execute("SELECT key_alias, provider FROM shards ORDER BY key_alias")
        for alias, provider in cursor.fetchall():
            keys.append({"alias": alias, "provider": provider})
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    return keys


def _discover_proxy_port(home: WorthlessHome) -> int | None:
    """Find proxy port from PID file or WORTHLESS_PORT env var."""
    # Check env var first
    port_env = os.environ.get("WORTHLESS_PORT")
    if port_env:
        try:
            return int(port_env)
        except ValueError:
            pass

    # Check PID file (format: "pid\nport\n")
    pid_file = home.base_dir / "proxy.pid"
    if pid_file.exists():
        info = read_pid(pid_file)
        if info is not None:
            return info[1]

    return None


def _check_proxy_health(port: int) -> dict[str, Any]:
    """Hit /healthz and return proxy status dict."""
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2.0)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "healthy": True,
                "port": port,
                "mode": data.get("mode", "up"),
            }
    except Exception:
        pass

    return {"healthy": False, "port": port, "mode": None}


def register_status_commands(app: typer.Typer) -> None:
    """Register the status command on the Typer app."""

    @app.command()
    def status() -> None:
        """Show enrolled keys and proxy health."""
        console = get_console()

        home = resolve_home()

        # Enrolled keys
        keys: list[dict[str, str]] = []
        if home is not None:
            keys = _list_enrolled_keys(home)

        # Proxy health
        proxy_info: dict[str, Any] = {"healthy": False, "port": None, "mode": None}
        if home is not None:
            port = _discover_proxy_port(home)
            if port is not None:
                proxy_info = _check_proxy_health(port)

        # Output
        if console.json_mode:
            result = {"keys": keys, "proxy": proxy_info}
            sys.stdout.write(json.dumps(result, default=str) + "\n")
            sys.stdout.flush()
        else:
            if not keys:
                console.print_warning("No keys enrolled.")
            else:
                lines = ["Enrolled keys:"]
                for k in keys:
                    lines.append(f"  {k['alias']}  {k['provider']}  PROTECTED")
                sys.stderr.write("\n".join(lines) + "\n\n")

            if proxy_info["healthy"]:
                sys.stderr.write(
                    f"Proxy: running on 127.0.0.1:{proxy_info['port']}"
                    f" (mode: {proxy_info['mode']})\n"
                )
            else:
                sys.stderr.write("Proxy: not running\n")
            sys.stderr.flush()

        raise typer.Exit(code=0)

```

### `src/worthless/storage/repository.py`

```python
"""Encrypted shard repository backed by SQLite (STOR-01, STOR-02)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, NamedTuple

import aiosqlite
from cryptography.fernet import Fernet

from worthless.storage.schema import init_db


class EncryptedShard(NamedTuple):
    """Raw encrypted shard record — no Fernet decryption applied."""

    shard_b_enc: bytes
    commitment: bytes
    nonce: bytes
    provider: str

    def __repr__(self) -> str:
        return (
            f"EncryptedShard(shard_b_enc=<{len(self.shard_b_enc)} bytes>, "
            f"commitment=<{len(self.commitment)} bytes>, "
            f"nonce=<{len(self.nonce)} bytes>, provider={self.provider!r})"
        )


@dataclass
class EnrollmentRecord:
    """A single enrollment binding a key alias to a var name and optional env path."""

    key_alias: str
    var_name: str
    env_path: str | None = None


@dataclass
class StoredShard:
    """Decrypted shard record with bytearray fields (SR-01 compliance)."""

    shard_b: bytearray
    commitment: bytearray
    nonce: bytearray
    provider: str

    def __repr__(self) -> str:
        return (
            f"StoredShard(shard_b=<{len(self.shard_b)} bytes>, "
            f"commitment=<{len(self.commitment)} bytes>, "
            f"nonce=<{len(self.nonce)} bytes>, provider={self.provider!r})"
        )

    def zero(self) -> None:
        """Zero all cryptographic fields in place (SR-02)."""
        for field in (self.shard_b, self.commitment, self.nonce):
            field[:] = b"\x00" * len(field)


class ShardRepository:
    """Async repository that encrypts Shard B at rest with Fernet.

    Each public method opens its own ``aiosqlite`` connection (simple PoC
    approach -- connection pooling is not needed at this stage).

    .. todo:: Use a persistent connection or pool before production (STOR-01).
    """

    def __init__(self, db_path: str, fernet_key: bytes) -> None:
        self._db_path = db_path
        self._fernet = Fernet(fernet_key)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open a connection with foreign keys enabled."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        await init_db(self._db_path)

    # ------------------------------------------------------------------
    # Shard CRUD
    # ------------------------------------------------------------------

    async def store(
        self,
        alias: str,
        shard: StoredShard,
    ) -> None:
        """Encrypt *shard.shard_b* with Fernet and INSERT into the shards table.

        Accepts bytearray or bytes for shard_b (converts to bytes for Fernet).
        Raises ``aiosqlite.IntegrityError`` if *alias* already exists.
        """
        shard_b_enc = self._fernet.encrypt(bytes(shard.shard_b))
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO shards (key_alias, shard_b_enc, commitment, nonce, provider) "
                "VALUES (?, ?, ?, ?, ?)",
                (alias, shard_b_enc, bytes(shard.commitment), bytes(shard.nonce), shard.provider),
            )
            await db.commit()

    async def fetch_encrypted(self, alias: str) -> EncryptedShard | None:
        """Return the raw encrypted shard without Fernet decryption, or *None*.

        This enables gate-before-decrypt: the rules engine can evaluate
        before any key material is decrypted (CRYP-05).
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT shard_b_enc, commitment, nonce, provider FROM shards WHERE key_alias = ?",
                (alias,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return EncryptedShard(
                shard_b_enc=bytes(row["shard_b_enc"]),
                commitment=bytes(row["commitment"]),
                nonce=bytes(row["nonce"]),
                provider=row["provider"],
            )

    def decrypt_shard(self, encrypted: EncryptedShard) -> StoredShard:
        """Fernet-decrypt an :class:`EncryptedShard` into a :class:`StoredShard`.

        All byte fields are wrapped in ``bytearray`` per SR-01.
        """
        shard_b = self._fernet.decrypt(encrypted.shard_b_enc)
        return StoredShard(
            shard_b=bytearray(shard_b),
            commitment=bytearray(encrypted.commitment),
            nonce=bytearray(encrypted.nonce),
            provider=encrypted.provider,
        )

    async def retrieve(self, alias: str) -> StoredShard | None:
        """Decrypt and return a :class:`StoredShard` or *None*.

        Backward-compatible convenience that calls fetch_encrypted + decrypt_shard.
        """
        encrypted = await self.fetch_encrypted(alias)
        if encrypted is None:
            return None
        return self.decrypt_shard(encrypted)

    async def delete(self, alias: str) -> bool:
        """Delete the shard record for *alias*. Returns True if deleted."""
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM shards WHERE key_alias = ?", (alias,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_keys(self) -> list[str]:
        """Return a list of all enrolled key aliases."""
        async with self._connect() as db:
            cursor = await db.execute("SELECT key_alias FROM shards")
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def set_metadata(self, key: str, value: str) -> None:
        """Upsert a metadata key/value pair."""
        async with self._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (key, value),
            )
            await db.commit()

    async def get_metadata(self, key: str) -> str | None:
        """Return the metadata value for *key*, or *None*."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # Enrollment CRUD
    # ------------------------------------------------------------------

    async def store_enrolled(
        self,
        alias: str,
        shard: StoredShard,
        *,
        var_name: str,
        env_path: str | None = None,
    ) -> None:
        """Atomically store a shard and its enrollment record.

        If the shard already exists (same alias), only the enrollment row
        is inserted.
        """
        shard_b_enc = self._fernet.encrypt(bytes(shard.shard_b))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "INSERT OR IGNORE INTO shards (key_alias, shard_b_enc, commitment, nonce, provider) "
                "VALUES (?, ?, ?, ?, ?)",
                (alias, shard_b_enc, bytes(shard.commitment), bytes(shard.nonce), shard.provider),
            )
            await db.execute(
                "INSERT OR IGNORE INTO enrollments (key_alias, var_name, env_path) "
                "VALUES (?, ?, ?)",
                (alias, var_name, env_path),
            )
            await db.commit()

    async def get_enrollment(
        self, alias: str, env_path: str | None = None
    ) -> EnrollmentRecord | None:
        """Return the enrollment for *alias*.

        If *env_path* is given, filter by exact match. Otherwise return the
        first enrollment for the alias (useful when only one exists).
        """
        async with self._connect() as db:

            if env_path is None:
                cursor = await db.execute(
                    "SELECT key_alias, var_name, env_path FROM enrollments "
                    "WHERE key_alias = ? LIMIT 1",
                    (alias,),
                )
            else:
                cursor = await db.execute(
                    "SELECT key_alias, var_name, env_path FROM enrollments "
                    "WHERE key_alias = ? AND env_path = ?",
                    (alias, env_path),
                )
            row = await cursor.fetchone()
            if row is None:
                return None
            return EnrollmentRecord(key_alias=row[0], var_name=row[1], env_path=row[2])

    async def list_enrollments(self, alias: str | None = None) -> list[EnrollmentRecord]:
        """Return enrollment records, optionally filtered by *alias*."""
        async with self._connect() as db:

            if alias is not None:
                cursor = await db.execute(
                    "SELECT key_alias, var_name, env_path FROM enrollments WHERE key_alias = ?",
                    (alias,),
                )
            else:
                cursor = await db.execute(
                    "SELECT key_alias, var_name, env_path FROM enrollments ORDER BY key_alias"
                )
            rows = await cursor.fetchall()
            return [EnrollmentRecord(key_alias=r[0], var_name=r[1], env_path=r[2]) for r in rows]

    async def delete_enrollment(self, alias: str, env_path: str) -> bool:
        """Delete a single enrollment row. Returns True if deleted."""
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM enrollments WHERE key_alias = ? AND env_path = ?",
                (alias, env_path),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_enrolled(self, alias: str) -> bool:
        """Delete the shard and all enrollments for *alias* (CASCADE).

        Returns True if deleted.
        """
        async with self._connect() as db:

            cursor = await db.execute(
                "DELETE FROM shards WHERE key_alias = ?", (alias,)
            )
            await db.commit()
            return cursor.rowcount > 0

```

### `src/worthless/storage/schema.py`

```python
"""SQLite schema and initialisation for encrypted shard storage."""

from __future__ import annotations

import aiosqlite

SCHEMA = """\
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS shards (
    key_alias   TEXT PRIMARY KEY,
    shard_b_enc BLOB NOT NULL,
    commitment  BLOB NOT NULL,
    nonce       BLOB NOT NULL,
    provider    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spend_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key_alias  TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    model      TEXT,
    provider   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS enrollment_config (
    key_alias      TEXT PRIMARY KEY,
    spend_cap      REAL,
    rate_limit_rps REAL NOT NULL DEFAULT 100.0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS enrollments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key_alias  TEXT NOT NULL REFERENCES shards(key_alias) ON DELETE CASCADE,
    var_name   TEXT NOT NULL,
    env_path   TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(key_alias, env_path)
);

CREATE INDEX IF NOT EXISTS idx_spend_log_alias ON spend_log (key_alias);
CREATE INDEX IF NOT EXISTS idx_enrollments_alias ON enrollments (key_alias);
CREATE UNIQUE INDEX IF NOT EXISTS idx_enrollments_null_path
    ON enrollments (key_alias) WHERE env_path IS NULL;
"""


async def init_db(db_path: str) -> None:
    """Create tables and enable WAL journal mode."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA journal_mode=WAL")
        await db.commit()

```

### `src/worthless/cli/bootstrap.py`

```python
"""First-run ~/.worthless/ initialization and lock management."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

from cryptography.fernet import Fernet

from worthless.cli.errors import ErrorCode, WorthlessError

_DEFAULT_BASE = Path.home() / ".worthless"
_STALE_LOCK_SECONDS = 300  # 5 minutes


@dataclass
class WorthlessHome:
    """Paths within the ``~/.worthless/`` directory tree."""

    base_dir: Path = field(default_factory=lambda: _DEFAULT_BASE)

    @property
    def db_path(self) -> Path:
        return self.base_dir / "worthless.db"

    @property
    def fernet_key_path(self) -> Path:
        return self.base_dir / "fernet.key"

    @property
    def shard_a_dir(self) -> Path:
        return self.base_dir / "shard_a"

    @property
    def lock_file(self) -> Path:
        return self.base_dir / ".lock-in-progress"

    @property
    def fernet_key(self) -> bytes:
        """Read the Fernet key from disk."""
        return self.fernet_key_path.read_bytes().strip()


def ensure_home(base_dir: Path | None = None) -> WorthlessHome:
    """Create ``~/.worthless/`` structure on first run (idempotent).

    Creates directories with 0700 permissions, generates a Fernet key if
    missing, and initialises the SQLite database.
    """
    home = WorthlessHome(base_dir=base_dir or _DEFAULT_BASE)

    # Create directories
    home.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    home.shard_a_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Ensure permissions are correct even if dir already existed
    os.chmod(home.base_dir, 0o700)
    os.chmod(home.shard_a_dir, 0o700)

    # Generate Fernet key if missing
    if not home.fernet_key_path.exists():
        key = Fernet.generate_key()
        fd = os.open(
            str(home.fernet_key_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.write(fd, key)
            os.write(fd, b"\n")
        finally:
            os.close(fd)

    # Initialise database (idempotent — CREATE TABLE IF NOT EXISTS)
    _init_db(home)

    return home


def _init_db(home: WorthlessHome) -> None:
    """Create the SQLite database using the canonical schema."""
    import sqlite3

    from worthless.storage.schema import SCHEMA

    conn = sqlite3.connect(str(home.db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
    finally:
        conn.close()

    # Restrict DB file permissions
    os.chmod(str(home.db_path), 0o600)


@contextmanager
def acquire_lock(home: WorthlessHome) -> Generator[None, None, None]:
    """Acquire an exclusive lock file using O_CREAT|O_EXCL."""
    check_stale_lock(home)
    try:
        fd = os.open(
            str(home.lock_file),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(fd)
    except FileExistsError:
        raise WorthlessError(
            ErrorCode.LOCK_IN_PROGRESS,
            "Another worthless operation is in progress. "
            "Remove ~/.worthless/.lock-in-progress if stale.",
        )
    try:
        yield
    finally:
        try:
            home.lock_file.unlink()
        except FileNotFoundError:
            pass


def get_home() -> WorthlessHome:
    """Resolve WorthlessHome from WORTHLESS_HOME env var or default."""
    env_home = os.environ.get("WORTHLESS_HOME")
    if env_home:
        return ensure_home(Path(env_home))
    return ensure_home()


def resolve_home() -> WorthlessHome | None:
    """Try to load WorthlessHome; return None if not initialized."""
    try:
        env_home = os.environ.get("WORTHLESS_HOME")
        if env_home:
            base = Path(env_home)
            if base.exists():
                return ensure_home(base)
            return None
        default = Path.home() / ".worthless"
        if default.exists():
            return ensure_home(default)
        return None
    except Exception:
        return None


def check_stale_lock(home: WorthlessHome) -> None:
    """Remove stale lock files (> 5 min old), raise on fresh locks."""
    if not home.lock_file.exists():
        return
    age = time.time() - home.lock_file.stat().st_mtime
    if age > _STALE_LOCK_SECONDS:
        home.lock_file.unlink(missing_ok=True)
    else:
        raise WorthlessError(
            ErrorCode.LOCK_IN_PROGRESS,
            f"Lock file is {int(age)}s old (< {_STALE_LOCK_SECONDS}s). "
            "Another operation may be running.",
        )

```

### `src/worthless/cli/scanner.py`

```python
"""Key pattern detection with entropy and decoy awareness."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from worthless.cli.dotenv_rewriter import shannon_entropy
from worthless.cli.key_patterns import ENTROPY_THRESHOLD, KEY_PATTERN, detect_provider

_VAR_NAME_RE = re.compile(r"(\w+)\s*$")


@dataclass
class ScanFinding:
    """A detected API key occurrence in a scanned file."""

    file: str
    line: int
    var_name: str | None
    provider: str
    is_protected: bool
    value_preview: str  # fully masked by default


def scan_files(
    paths: list[Path],
) -> list[ScanFinding]:
    """Scan files for API key patterns.

    Each file is read line-by-line. Matches with entropy below the
    threshold are skipped (likely placeholders). If *enrollment_data*
    is provided and contains the matched value, the finding is marked
    ``is_protected=True``.
    """
    # TODO: Implement hash-based enrollment lookup for defense-in-depth.
    # Currently decoy detection relies solely on entropy threshold.
    findings: list[ScanFinding] = []

    for path in paths:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in KEY_PATTERN.finditer(line):
                value = match.group(0)
                if shannon_entropy(value) < ENTROPY_THRESHOLD:
                    continue
                provider = detect_provider(value)
                if provider is None:
                    continue

                # Try to extract var_name from KEY=VALUE or KEY = "VALUE"
                var_name = _extract_var_name(line, match.start())

                is_protected = False

                findings.append(ScanFinding(
                    file=str(path),
                    line=line_no,
                    var_name=var_name,
                    provider=provider,
                    is_protected=is_protected,
                    value_preview=_mask(value),
                ))
    return findings


def _extract_var_name(line: str, value_start: int) -> str | None:
    """Try to find a variable name before the value in the line."""
    prefix = line[:value_start].rstrip()
    if prefix.endswith("="):
        prefix = prefix[:-1].rstrip().strip('"').strip("'")
        m = _VAR_NAME_RE.search(prefix)
        return m.group(1) if m else None
    return None


def _mask(value: str) -> str:
    """Mask all but provider prefix of a key value."""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****"


def format_sarif(findings: list[ScanFinding], tool_version: str) -> dict:
    """Format findings as SARIF v2.1.0.

    Returns a dict suitable for ``json.dumps()``.
    """
    results = []
    for f in findings:
        result: dict = {
            "ruleId": "worthless/exposed-api-key",
            "level": "warning" if f.is_protected else "error",
            "message": {
                "text": f"Exposed {f.provider} API key"
                + (f" in variable {f.var_name}" if f.var_name else "")
                + (" (protected by worthless)" if f.is_protected else ""),
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file},
                        "region": {"startLine": f.line},
                    }
                }
            ],
        }
        results.append(result)

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "worthless",
                        "version": tool_version,
                        "rules": [
                            {
                                "id": "worthless/exposed-api-key",
                                "shortDescription": {
                                    "text": "Exposed API key detected"
                                },
                            }
                        ],
                    }
                },
                "results": results,
            }
        ],
    }

```

### `src/worthless/cli/dotenv_rewriter.py`

```python
"""Atomic prefix-preserving .env key replacement and scanning."""

from __future__ import annotations

import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from worthless.cli.key_patterns import ENTROPY_THRESHOLD, KEY_PATTERN, detect_provider


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of string *s* in bits."""
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in counts.values()
    )


def scan_env_keys(env_path: Path) -> list[tuple[str, str, str]]:
    """Find API keys in a ``.env`` file.

    Returns a list of ``(var_name, value, provider)`` tuples for lines
    whose value matches a known provider prefix and has entropy above
    the threshold (filtering out placeholders).
    """
    results: list[tuple[str, str, str]] = []
    text = env_path.read_text()
    for line in text.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if "=" not in line_stripped:
            continue
        var_name, _, raw_value = line_stripped.partition("=")
        var_name = var_name.strip()
        value = raw_value.strip().strip("\"'")
        if not KEY_PATTERN.search(value):
            continue
        if shannon_entropy(value) < ENTROPY_THRESHOLD:
            continue
        provider = detect_provider(value)
        if provider:
            results.append((var_name, value, provider))
    return results


def rewrite_env_key(env_path: Path, var_name: str, new_value: str) -> None:
    """Atomically replace the value of *var_name* in *env_path*.

    Preserves comments, blank lines, ordering, and all other variables.
    Raises ``KeyError`` if *var_name* is not found.
    """
    text = env_path.read_text()
    lines = text.splitlines(keepends=True)
    found = False
    new_lines: list[str] = []

    pattern = re.compile(rf"^{re.escape(var_name)}\s*=")

    for line in lines:
        if pattern.match(line.lstrip()):
            # Preserve any trailing newline from the original line
            eol = "\n" if line.endswith("\n") else ""
            new_lines.append(f"{var_name}={new_value}{eol}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        raise KeyError(f"Variable {var_name!r} not found in {env_path}")

    # Atomic write: write to temp file, then os.replace
    dir_path = env_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), prefix=".env.tmp.")
    fd_closed = False
    try:
        os.write(fd, "".join(new_lines).encode())
        os.close(fd)
        fd_closed = True
        os.replace(tmp_path, str(env_path))
    except BaseException:
        if not fd_closed:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

```

### `src/worthless/cli/process.py`

```python
"""Process lifecycle — pipe death detection, signal forwarding, PID files.

Shared infrastructure for ``wrap`` (ephemeral proxy + child) and ``up``
(standalone daemon) commands.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Regex to capture the port from uvicorn's startup line
_UVICORN_PORT_RE = re.compile(r"Uvicorn running on http://[\d.]+:(\d+)")


# ---------------------------------------------------------------------------
# Core dump suppression
# ---------------------------------------------------------------------------


def disable_core_dumps() -> None:
    """Set RLIMIT_CORE to (0, 0) to prevent core dumps leaking key material.

    Silently ignored on platforms that don't support it (e.g. some CI runners).
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# Liveness pipe — parent holds write_fd, proxy watches read_fd for EOF
# ---------------------------------------------------------------------------


def create_liveness_pipe() -> tuple[int, int]:
    """Create an OS pipe for death detection.

    Returns:
        (read_fd, write_fd).  Pass *read_fd* to the proxy via
        ``WORTHLESS_LIVENESS_FD``.  Keep *write_fd* open in the parent;
        closing it (or parent death) signals EOF to the proxy.
    """
    return os.pipe()


# ---------------------------------------------------------------------------
# Proxy spawning
# ---------------------------------------------------------------------------


def spawn_proxy(
    env: dict[str, str],
    port: int = 0,
    liveness_fd: int | None = None,
) -> tuple[subprocess.Popen, int]:
    """Start uvicorn with the proxy app.

    Args:
        env: Environment variables for the proxy (WORTHLESS_*).
        port: Port to bind.  ``0`` means OS-assigned random port.
        liveness_fd: If set, passed to the child and advertised via
            ``WORTHLESS_LIVENESS_FD``.

    Returns:
        (process, actual_port).
    """
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "worthless.proxy.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]

    # Pass Fernet key via inherited fd instead of env var (avoids /proc/PID/environ leak)
    fernet_key = env.pop("WORTHLESS_FERNET_KEY", None)
    fernet_fd: int | None = None
    if fernet_key:
        r_fd, w_fd = os.pipe()
        os.write(w_fd, fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
        os.close(w_fd)
        fernet_fd = r_fd

    # Build subprocess environment
    full_env = {**os.environ, **env, "WORTHLESS_ALLOW_INSECURE": env.get("WORTHLESS_ALLOW_INSECURE", "true")}
    if fernet_fd is not None:
        full_env["WORTHLESS_FERNET_FD"] = str(fernet_fd)

    pass_fds: list[int] = []
    if liveness_fd is not None:
        full_env["WORTHLESS_LIVENESS_FD"] = str(liveness_fd)
        pass_fds.append(liveness_fd)
    if fernet_fd is not None:
        pass_fds.append(fernet_fd)

    proc = subprocess.Popen(
        cmd,
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        process_group=0,
        pass_fds=tuple(pass_fds),
    )

    if port == 0:
        # Parse actual port from uvicorn output
        actual_port = _parse_uvicorn_port(proc, timeout=15.0)
    else:
        actual_port = port

    return proc, actual_port


def _parse_uvicorn_port(proc: subprocess.Popen, timeout: float = 15.0) -> int:
    """Read uvicorn stdout until we find the port announcement."""
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None

    # Read output in a thread so we can impose a deadline
    lines: list[str] = []

    def _reader():
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            lines.append(line)
            m = _UVICORN_PORT_RE.search(line)
            if m:
                return

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=timeout)

    for line in lines:
        m = _UVICORN_PORT_RE.search(line)
        if m:
            return int(m.group(1))

    raise RuntimeError(
        f"Could not parse uvicorn port within {timeout}s. "
        f"Output so far: {''.join(lines[:20])}"
    )


# ---------------------------------------------------------------------------
# Health polling
# ---------------------------------------------------------------------------


def poll_health(port: int, timeout: float = 10.0) -> bool:
    """Poll ``GET /healthz`` until 200 or *timeout*.

    Returns True if healthy, False if timeout.
    """
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"

    with httpx.Client(timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = client.get(url)
                if resp.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException, OSError):
                pass
            time.sleep(0.3)

    return False


# ---------------------------------------------------------------------------
# PID file management
# ---------------------------------------------------------------------------


def write_pid(pid_path: Path, pid: int, port: int) -> None:
    """Write PID file with ``pid\\nport`` format (0600 permissions)."""
    fd = os.open(str(pid_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, f"{pid}\n{port}\n".encode())
    finally:
        os.close(fd)


def read_pid(pid_path: Path) -> tuple[int, int] | None:
    """Read PID file.  Returns ``(pid, port)`` or ``None`` if missing/corrupt."""
    try:
        text = pid_path.read_text().strip()
        parts = text.split("\n")
        if len(parts) < 2:
            return None
        return int(parts[0]), int(parts[1])
    except (FileNotFoundError, ValueError, IndexError):
        return None


def check_pid(pid: int) -> bool:
    """Return True if *pid* is alive (via ``os.kill(pid, 0)``)."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def cleanup_stale_pid(pid_path: Path) -> bool:
    """Remove PID file if the recorded process is dead.

    Returns True if reclaimed (file removed or didn't exist),
    False if the process is still alive.
    """
    info = read_pid(pid_path)
    if info is None:
        # Missing or corrupt — treat as reclaimed
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    pid, _port = info
    if check_pid(pid):
        return False  # Still alive

    pid_path.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Signal forwarding
# ---------------------------------------------------------------------------


def forward_signals(
    proxy: subprocess.Popen,
    child: subprocess.Popen | None,
) -> None:
    """Register handlers that forward SIGINT/SIGTERM to *proxy* and *child*.

    Uses process group kill (``os.killpg``) for robust cleanup.
    """

    def _handler(signum: int, _frame: object) -> None:
        for proc in (child, proxy):
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signum)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

```


---

## Scenarios to Verify

For EACH scenario:
1. **Trace** through the code step by step with function:line references
2. **Expected** behavior
3. **Actual** behavior from the code
4. **Bugs** found (or "None")
5. **Risk**: CRITICAL / HIGH / MEDIUM / LOW
6. **Tests**: exist / missing / partial

### A. User Scenarios
1. First-time lock on .env with one openai key
2. Re-lock on already-locked .env (idempotency)
3. Same key value in two vars (API_KEY=X and API_KEY_DEV=X, same value)
4. Same key in two .env files (project-a/.env and project-b/.env)
5. Lock → unlock roundtrip (exact content preservation)
6. enroll --key-stdin then lock (does lock protect the .env?)
7. unlock --alias when multiple enrollments exist (ambiguity handling)
8. unlock all keys from different .env files
9. wrap with only openai keys enrolled
10. wrap with google/xai keys enrolled (unsupported providers)

### B. Attacker Scenarios
11. Read ~/.worthless/ (same user local access) — full key reconstruction?
12. Read /proc/PID/environ of proxy — is fernet key visible?
13. Supply --alias="../fernet.key" to enroll
14. Supply --env=/etc/shadow to lock
15. Tamper with shard_a file (bit flip) — HMAC catches it?
16. Corrupt SQLite database — denial of service or key theft?

### C. System Scenarios
17. SIGKILL during lock after shard_a write but before DB write
18. SIGKILL during lock after DB write but before .env rewrite
19. Disk full during .env rewrite (shards already stored)
20. Two terminals run lock simultaneously
21. One terminal locks, another runs enroll (no lock file)
22. Power loss during SQLite commit

### D. Integration Scenarios
23. git pull reverts .env after lock — does re-lock work?
24. CI: enroll via stdin + wrap + test + unlock
25. Docker restart mid-session (ephemeral volume)

### E. Known Bugs to Verify

**KB-1**: Same key value in two vars — second copy left in plaintext because shard_a_path.exists() causes `continue` before rewrite_env_key runs.

**KB-2**: Error compensation in lock except block calls `delete_enrolled(alias)` which CASCADE-deletes ALL enrollments for that alias, destroying other successful enrollments.

**KB-3**: enroll command followed by lock — lock sees shard_a exists, skips, .env stays unprotected.

**KB-4**: O_EXCL crash if shard_a file is orphaned from previous crash.

For each KB: state whether STILL PRESENT or FIXED, with exact code path.

---

## Final Request

After all scenarios: **list any additional scenarios not covered above that could cause data loss, security exposure, or user confusion.** Focus on permanent key loss, silent exposure despite "locked" state, and false healthy reports.
