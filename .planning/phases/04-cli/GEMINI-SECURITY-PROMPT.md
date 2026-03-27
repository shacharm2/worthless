# Security Review: Worthless CLI — Split-Key Reverse Proxy

You are performing a comprehensive security audit of **Worthless**, a split-key reverse proxy that protects API keys from theft or accidental exposure. The tool:

1. **Scans** `.env` files for API keys (OpenAI, Anthropic, etc.)
2. **Splits** each key into two shards using XOR splitting with HMAC commitment
3. **Stores** shard A as a file (`~/.worthless/shard_a/<alias>`) and shard B encrypted (Fernet/AES-128-CBC) in SQLite
4. **Replaces** the original key in `.env` with a low-entropy decoy
5. **Reconstructs** the key at runtime inside a localhost reverse proxy that intercepts API calls
6. The proxy injects `{PROVIDER}_BASE_URL=http://127.0.0.1:<port>` so SDK calls route through it transparently

The threat model assumes: the developer's machine is partially compromised (malware can read files but may not have root), or `.env` files leak via git/logs/screenshots.

---

## Source Files Under Review

Review each file below for security vulnerabilities. Pay special attention to cryptographic operations, key material handling, file permissions, process isolation, and race conditions.

---

### File 1: `src/worthless/cli/commands/lock.py`

```python
"""Lock command — scan .env, split keys, store shards, rewrite with decoys."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, acquire_lock, ensure_home, get_home
from worthless.cli.console import get_console
from worthless.cli.dotenv_rewriter import rewrite_env_key, scan_env_keys
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.key_patterns import detect_prefix, detect_provider
from worthless.crypto.splitter import split_key
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
                meta_path.write_text(json.dumps({
                    "var_name": var_name,
                    "env_path": str(env_path.resolve()),
                }))

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
        key: str = typer.Option(..., "--key", "-k", help="API key to enroll"),
        provider: str = typer.Option(..., "--provider", "-p", help="Provider name"),
    ) -> None:
        """Enroll a single API key (scripting/CI primitive)."""
        console = get_console()
        home = get_home()
        try:
            _enroll_single(alias, key, provider, home)
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
```

---

### File 2: `src/worthless/cli/commands/unlock.py`

```python
"""Unlock command — reconstruct keys from shards, restore .env, clean up."""

from __future__ import annotations

import asyncio
import json
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
        home = get_home()
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
```

---

### File 3: `src/worthless/cli/commands/wrap.py`

```python
"""wrap command — ephemeral proxy + child process lifecycle.

``worthless wrap python main.py`` starts a transparent proxy on a random port,
injects ``{PROVIDER}_BASE_URL`` env vars so API calls route through it, runs
the child, and cleans up when the child exits.
"""

from __future__ import annotations

import os
import secrets
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

    Scans shard_a directory for files without .meta extension,
    reads the corresponding .meta file to get provider info.
    """
    providers: set[str] = set()
    shard_dir = home.shard_a_dir
    if not shard_dir.exists():
        return []

    for entry in shard_dir.iterdir():
        if entry.suffix == ".meta":
            continue
        # Extract provider from alias (format: "provider-hash8")
        name = entry.name
        if "-" in name:
            provider = name.rsplit("-", 1)[0]
            providers.add(provider)

    return sorted(providers)


def _build_child_env(
    port: int,
    providers: list[str],
    session_token: str,
) -> dict[str, str]:
    """Build environment for the child process.

    Inherits the current env and adds:
    - {PROVIDER}_BASE_URL for each enrolled provider
    - WORTHLESS_SESSION_TOKEN for proxy auth
    """
    env = dict(os.environ)
    for provider in providers:
        env_var = _PROVIDER_ENV_MAP.get(provider)
        if env_var:
            env[env_var] = f"http://127.0.0.1:{port}"
    env["WORTHLESS_SESSION_TOKEN"] = session_token
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

        # Generate session token
        session_token = secrets.token_urlsafe(32)

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
        child_env = _build_child_env(port, providers, session_token)

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

---

### File 4: `src/worthless/cli/commands/up.py`

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

        full_env = {
            **os.environ,
            **proxy_env,
            "WORTHLESS_ALLOW_INSECURE": proxy_env.get("WORTHLESS_ALLOW_INSECURE", "true"),
        }

        # Start detached process
        proc = subprocess.Popen(
            cmd,
            env=full_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # setsid equivalent
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

---

### File 5: `src/worthless/cli/process.py`

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

    # Build subprocess environment
    full_env = {**os.environ, **env, "WORTHLESS_ALLOW_INSECURE": env.get("WORTHLESS_ALLOW_INSECURE", "true")}

    pass_fds: tuple[int, ...] = ()
    if liveness_fd is not None:
        full_env["WORTHLESS_LIVENESS_FD"] = str(liveness_fd)
        pass_fds = (liveness_fd,)

    proc = subprocess.Popen(
        cmd,
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        process_group=0,
        pass_fds=pass_fds,
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
    """Write PID file with ``pid\\nport`` format."""
    pid_path.write_text(f"{pid}\n{port}\n")


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

### File 6: `src/worthless/cli/bootstrap.py`

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
    """Create the SQLite database with the shards table."""
    import sqlite3

    conn = sqlite3.connect(str(home.db_path))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS shards (
                key_alias TEXT PRIMARY KEY,
                shard_b_enc BLOB NOT NULL,
                commitment BLOB NOT NULL,
                nonce BLOB NOT NULL,
                provider TEXT NOT NULL
            )"""
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def acquire_lock(home: WorthlessHome) -> Generator[None, None, None]:
    """Acquire an exclusive lock file using O_CREAT|O_EXCL."""
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

---

### File 7: `src/worthless/cli/scanner.py`

```python
"""Key pattern detection with entropy and decoy awareness."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from worthless.cli.bootstrap import WorthlessHome
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


def load_enrollment_data(home: WorthlessHome | None) -> set[str]:
    """Read shard_a files to get known decoy values.

    Returns an empty set if *home* is ``None`` (CI mode) or if no
    shard_a directory exists.
    """
    if home is None:
        return set()
    shard_a_dir = home.shard_a_dir
    if not Path(shard_a_dir).exists():
        return set()
    values: set[str] = set()
    for f in Path(shard_a_dir).iterdir():
        if not f.is_file() or f.suffix == ".meta":
            continue
        try:
            values.add(f.read_text().strip())
        except (UnicodeDecodeError, OSError):
            # Shard files are binary — skip them gracefully
            continue
    return values


def scan_files(
    paths: list[Path],
    enrollment_data: set[str] | None = None,
) -> list[ScanFinding]:
    """Scan files for API key patterns.

    Each file is read line-by-line. Matches with entropy below the
    threshold are skipped (likely placeholders). If *enrollment_data*
    is provided and contains the matched value, the finding is marked
    ``is_protected=True``.
    """
    findings: list[ScanFinding] = []
    enrolled = enrollment_data or set()

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

                is_protected = value in enrolled

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

---

### File 8: `src/worthless/cli/dotenv_rewriter.py`

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
    try:
        os.write(fd, "".join(new_lines).encode())
        os.close(fd)
        os.replace(tmp_path, str(env_path))
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
```

---

## Security Review Questions

Please analyze the code above and provide a detailed security assessment covering each of the following areas. For each finding, rate severity (Critical / High / Medium / Low / Informational) and provide specific remediation recommendations with code examples where appropriate.

### 1. Memory Safety: Key Material Zeroing in Python

- Python uses reference-counted garbage collection with a generational GC. Evaluate whether `_zero_buf(bytearray)` actually prevents key material from persisting in memory.
- Can the CPython allocator copy bytearray contents during realloc? Does the `bytearray` type guarantee in-place mutation?
- Are there code paths where key material is held in a Python `str` (immutable, cannot be zeroed)? Trace every place where `.decode()` is called on key material.
- What about `json.dumps()`, `hashlib.sha256(api_key.encode())`, and f-string interpolation -- do these create unclearable copies?
- Is `sr.zero()` always reached in exception paths? Are there any missing `finally` blocks?
- Rate the realistic effectiveness of zeroing in CPython vs. PyPy.

### 2. Process Isolation Gaps

- The Fernet key is passed via environment variable (`WORTHLESS_FERNET_KEY`) to the proxy subprocess. Evaluate the security of this approach on Linux (`/proc/<pid>/environ`) and macOS.
- Is the session token (`WORTHLESS_SESSION_TOKEN`) generated in `wrap.py` actually validated by the proxy? What happens if the proxy ignores it?
- The child process inherits `WORTHLESS_SESSION_TOKEN` in its env. Can a malicious child read it and directly query the proxy?
- Evaluate the liveness pipe mechanism -- can a process keep itself alive by holding the FD?
- `process_group=0` creates a new process group for the child. What are the security implications?

### 3. File Permission Attack Vectors

- Evaluate the TOCTOU gap between `shard_a_path.exists()` check and `os.open(..., O_CREAT|O_EXCL)` in `lock.py`.
- The `.meta` files are written with `meta_path.write_text()` (default umask). What permissions do they get?
- `write_pid()` uses `pid_path.write_text()` -- what permissions does the PID file get?
- `rewrite_env_key()` uses `tempfile.mkstemp()` then `os.replace()`. Are the temp file permissions correct?
- What happens if `~/.worthless/` is a symlink to another directory?
- Can a local attacker create `~/.worthless/` before the user runs `worthless` for the first time?

### 4. Race Conditions

- In `_lock_keys()`, there is a gap between `scan_env_keys()` reading the .env and `rewrite_env_key()` writing the decoy. What happens if another process modifies .env in between?
- The lock file mechanism uses `O_CREAT|O_EXCL` but has no PID written to it. How do you detect stale locks from crashed processes vs. legitimate locks?
- `_start_daemon()` writes the PID file before confirming health. What if the proxy crashes immediately after PID write?
- `cleanup_stale_pid()` has a TOCTOU between `check_pid()` and `unlink()`. Can this be exploited?
- In `unlock`, shard files are deleted before the DB entry (`asyncio.run(repo.delete(alias))`). What happens if the process crashes between these operations?

### 5. Cryptographic Implementation Review

- XOR splitting: Is the splitting scheme information-theoretically secure? What entropy source is used for shard generation?
- The commitment scheme uses HMAC. Is the nonce generated with a CSPRNG? Is the HMAC construction correct (key, message ordering)?
- Fernet (AES-128-CBC + HMAC-SHA256): Is Fernet appropriate for this use case? What are its limitations (e.g., no associated data, fixed algorithm)?
- The Fernet key is stored in plaintext at `~/.worthless/fernet.key`. This is the single point of failure -- if stolen, all shard_b values can be decrypted. What mitigations exist?
- `_make_alias()` uses SHA-256 truncated to 8 hex chars (32 bits). What is the collision probability for realistic key counts? Is this a security risk or just a UX issue?
- The decoy generation in `_make_decoy()` derives from `shard_a` via SHA-256. Does the decoy leak information about shard_a?

### 6. Supply Chain and Dependency Concerns

- List all third-party dependencies visible in the imports (`cryptography`, `httpx`, `typer`, `uvicorn`). For each, assess:
  - Is it actively maintained?
  - Has it had known CVEs in the past 2 years?
  - Does it make network calls that could leak data?
- The proxy is started via `sys.executable -m uvicorn` -- can `sys.executable` be hijacked?
- `WORTHLESS_ALLOW_INSECURE` is hardcoded to `"true"` in proxy env. What does this flag control and what are the implications?

### 7. OWASP Top 10 Mapping

Map each relevant OWASP Top 10 (2021) category to findings in this codebase:

- **A01: Broken Access Control** -- File permissions, PID file manipulation, symlink attacks
- **A02: Cryptographic Failures** -- XOR splitting strength, Fernet key storage, key material in memory
- **A03: Injection** -- Can alias names or var_names contain path traversal characters?
- **A04: Insecure Design** -- Is the split-key model fundamentally sound? What does it actually protect against?
- **A05: Security Misconfiguration** -- Default permissions, `WORTHLESS_ALLOW_INSECURE`, core dump suppression effectiveness
- **A06: Vulnerable Components** -- Dependency assessment
- **A07: Auth Failures** -- Session token validation, proxy authentication
- **A08: Data Integrity** -- Can shards be tampered with? Is the commitment scheme sufficient?
- **A09: Logging/Monitoring** -- Are security events logged? Can key material leak into logs?
- **A10: SSRF** -- The proxy forwards requests. Can it be tricked into hitting internal services?

### 8. Additional Questions

- What happens if the user runs `worthless lock` twice on the same .env file? Is idempotency truly guaranteed by the entropy threshold?
- The `enroll` command accepts `--key` as a CLI argument. This means the key appears in shell history, `ps` output, and `/proc/<pid>/cmdline`. How severe is this?
- In `unlock`, key material is printed to stdout as a recovery mechanism. Is this logged anywhere? Can it be captured by a terminal multiplexer's scrollback buffer?
- `scanner.py`'s `load_enrollment_data()` reads shard_a files as text. Since shards are binary XOR output, could this cause silent data corruption when comparing with scanned values?
- The `rewrite_env_key` error handler has `os.close(fd) if not os.get_inheritable(fd) else None` -- is this correct? What happens if `fd` is already closed?

---

## Output Format

For each section, provide:
1. **Finding title** (one line)
2. **Severity**: Critical / High / Medium / Low / Informational
3. **Description**: What the vulnerability is and why it matters
4. **Exploitation scenario**: How an attacker could exploit this
5. **Remediation**: Specific code changes or architectural recommendations
6. **References**: Relevant CVEs, OWASP entries, or Python documentation

End with an **Executive Summary** table listing all findings sorted by severity, and a **Prioritized Remediation Roadmap** with estimated effort for each fix.
