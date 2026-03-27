"""Scan command — detect exposed API keys with decoy awareness."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Optional

import typer

from worthless.cli.bootstrap import WorthlessHome, ensure_home
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError
from worthless.cli.scanner import ScanFinding, format_sarif, load_enrollment_data, scan_files


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


def _collect_deep_paths(explicit_paths: list[Path]) -> list[Path]:
    """Deep mode: fast paths + config files in project root + env dump."""
    import tempfile

    paths = _collect_fast_paths(explicit_paths)

    # Common config files
    for pattern in ["*.yml", "*.yaml", "*.toml", "*.json"]:
        for p in Path(".").glob(pattern):
            if p.is_file() and p not in paths:
                paths.append(p)

    # Dump os.environ to temp file and scan it
    env_lines = [f"{k}={v}" for k, v in os.environ.items()]
    if env_lines:
        fd, tmp = tempfile.mkstemp(prefix="worthless-env-", suffix=".env")
        try:
            os.write(fd, "\n".join(env_lines).encode())
            os.close(fd)
            paths.append(Path(tmp))
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass

    return paths


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

    for f in findings:
        status = "PROTECTED" if f.is_protected else "UNPROTECTED"
        preview = f.value_preview
        if show_suffix and not f.is_protected:
            # Read the actual file to get suffix
            try:
                from worthless.cli.key_patterns import KEY_PATTERN

                text = Path(f.file).read_text(errors="replace")
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
        format_: str = typer.Option("text", "--format", "-f", help="Output format: text, sarif, json"),
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

        try:
            # Collect files to scan
            explicit = list(paths) if paths else []
            if pre_commit:
                scan_paths = explicit
            elif deep:
                scan_paths = _collect_deep_paths(explicit)
            else:
                scan_paths = _collect_fast_paths(explicit)

            # Load enrollment data for decoy detection
            home = _resolve_home()
            enrollment_data = load_enrollment_data(home)

            # Run scan
            findings = scan_files(scan_paths, enrollment_data)

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
