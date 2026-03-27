"""Status command — show enrolled keys and proxy health."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.cli.console import get_console


def _resolve_home() -> WorthlessHome | None:
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

    # Check PID file
    pid_file = home.base_dir / "proxy.pid"
    if pid_file.exists():
        try:
            data = json.loads(pid_file.read_text())
            return int(data.get("port", 0)) or None
        except (json.JSONDecodeError, ValueError, OSError):
            pass

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

        home = _resolve_home()

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
