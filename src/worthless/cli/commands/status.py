"""Status command — show enrolled keys and proxy health."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

import httpx
import typer

from worthless.cli.bootstrap import WorthlessHome, resolve_home
from worthless.cli.console import get_console
from worthless.cli.errors import error_boundary
from worthless.cli.orphans import FIX_PHRASE, is_orphan
from worthless.cli.process import read_pid
from worthless.storage.repository import EnrollmentRecord


def _list_enrolled_keys(home: WorthlessHome) -> list[dict[str, str]]:
    """List enrolled aliases with PROTECTED/BROKEN state.

    BROKEN = every enrollment for this alias references a deleted ``.env``
    line. PROTECTED = at least one enrollment is healthy (recovery is
    still possible from that ``.env``). HF5 / worthless-gmky.
    """
    keys: list[dict[str, str]] = []
    if not home.db_path.exists():
        return keys

    conn = sqlite3.connect(str(home.db_path))
    try:
        # LEFT JOIN drives from `shards` (1:1 with alias) so each row is
        # guaranteed to carry the alias's provider — even when no
        # enrollment exists yet (var_name + env_path will be NULL but
        # provider is always populated).
        cursor = conn.execute(
            "SELECT s.key_alias, s.provider, e.var_name, e.env_path "
            "FROM shards s LEFT JOIN enrollments e ON s.key_alias = e.key_alias "
            "ORDER BY s.key_alias"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    # Group rows by alias and decide PROTECTED vs BROKEN per alias.
    by_alias: dict[str, dict[str, Any]] = {}
    for alias, provider, var_name, env_path in rows:
        entry = by_alias.setdefault(
            alias, {"alias": alias, "provider": provider, "enrollments": []}
        )
        if var_name is not None and env_path is not None:
            entry["enrollments"].append(
                EnrollmentRecord(key_alias=alias, var_name=var_name, env_path=env_path)
            )

    for entry in by_alias.values():
        enrollments: list[EnrollmentRecord] = entry["enrollments"]
        if not enrollments:
            # Shard with no enrollment row at all — HF5 scope doesn't cover
            # this edge state. Default to PROTECTED so we don't false-flag.
            entry["status"] = "PROTECTED"
        elif all(is_orphan(e) for e in enrollments):
            entry["status"] = "BROKEN"
        else:
            entry["status"] = "PROTECTED"
        entry.pop("enrollments")  # internal scratch, not part of the public dict
        keys.append(entry)

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
                    lines.append(f"  {k['alias']}  {k['provider']}  {k['status']}")
                sys.stderr.write("\n".join(lines) + "\n\n")
                # HF5: if any row is BROKEN, point the user at doctor.
                # Phrase tokens come from cli/orphans.py — single source of
                # truth shared with unlock/doctor/scan.
                if any(k["status"] == "BROKEN" for k in keys):
                    sys.stderr.write(
                        f"Can't restore the keys marked BROKEN — their .env "
                        f"line was deleted. Run `{FIX_PHRASE}` to clean up.\n\n"
                    )

            if proxy_info["healthy"]:
                sys.stderr.write(
                    f"Proxy: running on 127.0.0.1:{proxy_info['port']}"
                    f" (mode: {proxy_info['mode']})\n"
                )
                sys.stderr.write(f"Requests proxied: {proxy_info['requests_proxied']}\n")
            else:
                sys.stderr.write("Proxy: not running\n")
            sys.stderr.flush()

        raise typer.Exit(code=0)
