"""Status command — show enrolled keys and proxy health.

Trust-fix (2026-05-08 verification gauntlet): also reads
``$WORTHLESS_HOME/last-lock-status.json``. When the sentinel reports
DEGRADED state (lock-core succeeded but the OpenClaw integration stage
failed), status emits a ``[WARN]`` row AND exits non-zero so the
"five-minute-later" trust failure mode is closed: a stale terminal
session that swallowed the original ``lock`` exit code still gets
told the truth on the next ``worthless status`` invocation.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
import typer

from worthless.cli.bootstrap import WorthlessHome, resolve_home
from worthless.cli.console import get_console
from worthless.cli.errors import error_boundary
from worthless.cli.process import read_pid
from worthless.cli.sentinel import is_partial, read_sentinel


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
                "requests_proxied": data.get("requests_proxied", 0),
            }
    except Exception:  # noqa: S110 — proxy may not be running; absence is the expected default state  # nosec B110
        pass

    return {"healthy": False, "port": port, "mode": None, "requests_proxied": 0}


def register_status_commands(app: typer.Typer) -> None:
    """Register the status command on the Typer app."""

    @app.command()
    @error_boundary
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

        # Trust-fix sentinel (2026-05-08 gauntlet): persistent DEGRADED
        # state survives the terminal session that ran `worthless lock`.
        # If the last lock/unlock left a partial state, status MUST tell
        # the user — the original exit code may have been swallowed by CI
        # or shell-script `; my-app` chaining.
        sentinel: dict[str, Any] | None = None
        if home is not None:
            sentinel = read_sentinel(home.base_dir)
        degraded = is_partial(sentinel)

        # Output
        if console.json_mode:
            result = {
                "keys": keys,
                "proxy": proxy_info,
                "sentinel": sentinel,
                "degraded": degraded,
            }
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
                sys.stderr.write(f"Requests proxied: {proxy_info['requests_proxied']}\n")
            else:
                sys.stderr.write("Proxy: not running\n")

            if degraded:
                sys.stderr.write(
                    "\n[WARN] OpenClaw integration is broken — "
                    "your agent traffic may NOT be gated by worthless.\n"
                    "       Run `worthless doctor` to repair, or "
                    "`worthless unlock` to roll back.\n"
                )
            sys.stderr.flush()

        # Trust-fix exit: degraded sentinel = non-zero exit so callers
        # (CI scripts, shell pipelines, agents polling status) get the
        # signal even if they ignored the original lock exit code.
        raise typer.Exit(code=73 if degraded else 0)
