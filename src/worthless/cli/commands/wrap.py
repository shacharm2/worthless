"""wrap command -- ephemeral proxy + child process lifecycle.

``worthless wrap python main.py`` starts a transparent proxy on a random port
and runs the child against it. After 8rqs, the .env that ``worthless lock``
already rewrote IS the contract — wrap no longer mutates ``*_BASE_URL`` vars
in the child env. Its job is now process supervision only: spawn proxy,
spawn child, wait, clean up.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess  # nosec B404
import sys
import threading

import typer

from worthless.cli.bootstrap import WorthlessHome, get_home
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary, sanitize_exception
from worthless.cli.platform import fail_if_windows, popen_platform_kwargs
from worthless.cli.process import (
    build_proxy_env,
    create_liveness_pipe,
    disable_core_dumps,
    forward_signals,
    poll_health,
    spawn_proxy,
)
from worthless.storage.repository import ShardRepository

logger = logging.getLogger(__name__)


def _list_enrolled_aliases(home: WorthlessHome) -> list[tuple[str, str]]:
    """Return ``(alias, protocol)`` pairs. Empty when the DB is absent.

    Kept for the empty-DB guard in ``wrap`` (no enrolled keys → friendly
    error). The protocol field isn't used anymore but is preserved in the
    return shape so existing tests stay green.
    """
    if not home.db_path.exists():
        return []

    async def _query():
        repo = ShardRepository(str(home.db_path), home.fernet_key)
        await repo.initialize()
        rows = await repo.list_aliases_with_routing()
        return [(alias, protocol) for alias, _var, _url, protocol in rows]

    try:
        return asyncio.run(_query())
    except Exception:
        return []


def _build_child_env(
    port: int,
    aliases: list[tuple[str, str]],
) -> dict[str, str]:
    """Inherit the parent env unchanged.

    Pre-8rqs, this used to inject ``OPENAI_BASE_URL`` (or
    ``ANTHROPIC_BASE_URL``) per provider so the child SDK pointed at the
    local proxy. Post-8rqs, ``worthless lock`` already wrote the right
    values into the user's ``.env`` (preserving their var names — e.g.
    ``OPENROUTER_BASE_URL`` stays ``OPENROUTER_BASE_URL``). Wrap shouldn't
    overwrite that work.

    ``port`` and ``aliases`` are kept in the signature for backwards
    compatibility with existing tests; both are now ignored.
    """
    del port, aliases  # parameters retained for signature stability
    return dict(os.environ)


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
    @error_boundary
    def wrap(
        ctx: typer.Context,
        command: list[str] = typer.Argument(..., help="Command to run (e.g. python main.py)"),
    ) -> None:
        """Start ephemeral proxy, inject env vars, run COMMAND, clean up."""
        fail_if_windows()

        # Load home, verify keys enrolled
        home = get_home()
        aliases = _list_enrolled_aliases(home)
        if not aliases:
            raise WorthlessError(
                ErrorCode.KEY_NOT_FOUND,
                "No keys enrolled. Run 'worthless lock' first.",
            )

        # Suppress core dumps
        disable_core_dumps()

        # Create liveness pipe
        read_fd, write_fd = create_liveness_pipe()

        # Build proxy environment
        proxy_env = build_proxy_env(home)

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
            raise WorthlessError(
                ErrorCode.PROXY_UNREACHABLE,
                sanitize_exception(exc, generic="failed to start proxy"),
            ) from exc

        # Close read_fd in parent (proxy has it)
        os.close(read_fd)

        # Poll health
        healthy = poll_health(port, timeout=15.0)
        if not healthy:
            _cleanup_proxy(proxy, write_fd)
            raise WorthlessError(ErrorCode.PROXY_UNREACHABLE, "Proxy failed to become healthy")

        # Build child env with BASE_URL injection
        full_command = list(command) + ctx.args
        child_env = _build_child_env(port, aliases)

        # Spawn child
        try:
            child = subprocess.Popen(  # nosec B603
                full_command,
                env=child_env,
                **popen_platform_kwargs(detach=True),
            )
        except Exception as exc:
            _cleanup_proxy(proxy, write_fd)
            raise WorthlessError(
                ErrorCode.WRAP_CHILD_FAILED,
                sanitize_exception(exc, generic="failed to start child process"),
            ) from exc

        # Register signal forwarding
        forward_signals(proxy=proxy, child=child)

        # Monitor proxy in background -- warn on crash but don't kill child
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

        # Print post-run summary before cleanup
        try:
            from worthless.cli.commands.status import _check_proxy_health

            info = _check_proxy_health(port)
            count = info.get("requests_proxied", 0)
            if count:
                sys.stderr.write(f"worthless: proxied {count} requests\n")
                sys.stderr.flush()
        except Exception:  # noqa: S110 — best-effort summary  # nosec B110
            pass

        # Clean up proxy
        _cleanup_proxy(proxy, write_fd)

        raise typer.Exit(code=exit_code)
