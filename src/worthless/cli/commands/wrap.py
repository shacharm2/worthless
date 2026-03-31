"""wrap command -- ephemeral proxy + child process lifecycle.

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
    """List providers from the DB shards table."""
    import sqlite3

    if not home.db_path.exists():
        return []

    conn = sqlite3.connect(str(home.db_path))
    try:
        rows = conn.execute("SELECT DISTINCT provider FROM shards").fetchall()
        return sorted(r[0] for r in rows)
    finally:
        conn.close()


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

        try:
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

            # Monitor proxy in background -- warn on crash but don't kill child
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
        except (typer.Exit, SystemExit):
            raise
        except WorthlessError as exc:
            console.print_error(exc)
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            console.print_error(WorthlessError(ErrorCode.UNKNOWN, str(exc)))
            raise typer.Exit(code=1) from exc
