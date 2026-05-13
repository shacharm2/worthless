"""Scan command — detect exposed API keys with enrollment awareness."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import typer

from worthless.cli.bootstrap import get_home
from worthless.cli.code_scanner import CodeFinding, scan_for_hardcoded_provider_urls
from worthless.cli.console import get_console
from worthless.cli.errors import ErrorCode, WorthlessError, error_boundary
from worthless.cli.key_patterns import KEY_PATTERN
from worthless.cli.dotenv_rewriter import build_enrolled_locations
from worthless.cli.keystore import PLACEHOLDER_FERNET_KEY
from worthless.cli.orphans import FIX_PHRASE, PROBLEM_PHRASE, find_orphans
from worthless.cli.scanner import ScanFinding, format_sarif, scan_files
from worthless.storage.repository import EnrollmentRecord, ShardRepository


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
        for p in Path().glob(pattern):
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
            except Exception:  # noqa: S110 — fd cleanup on error path; can't recover usefully  # nosec B110
                pass

    return paths, tmp_path


def _format_human(
    findings: list[ScanFinding],
    orphans: list[EnrollmentRecord] | None = None,
    show_suffix: bool = False,
    is_tty: bool = True,
) -> str:
    """Format findings as human-readable text. HF5: ``orphans`` (broken DB
    rows whose ``.env`` line was deleted) get a dedicated ``Can't be
    restored:`` section + a ``, N broken`` segment in the trailing total.
    """
    orphans = orphans or []
    if not findings and not orphans:
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
                if f.file not in file_cache:
                    file_cache[f.file] = Path(f.file).read_text(errors="replace")
                text = file_cache[f.file]
                for line in text.splitlines():
                    for match in KEY_PATTERN.finditer(line):
                        value = match.group(0)
                        if preview.startswith(value[:4]):
                            preview = f.value_preview + "..." + value[-4:]
                            break
            except Exception:  # noqa: S110 — best-effort preview; display failure is non-critical  # nosec B110
                pass

        var_part = f" ({f.var_name})" if f.var_name else ""
        lines.append(f"  {f.file}:{f.line}  {f.provider}{var_part}  {status}  {preview}")

        if f.is_protected:
            protected_count += 1
        else:
            unprotected_count += 1

    # HF5: dedicated section for broken DB rows + recovery hint.
    # Section header carries the canonical PROBLEM_PHRASE; per-row drops
    # it to avoid the redundant "can't restore <alias> ... BROKEN" double-up.
    if orphans:
        lines.append("")
        lines.append(f"{PROBLEM_PHRASE.capitalize()} these keys (.env line deleted):")
        for o in orphans:
            lines.append(f"  {o.key_alias}  BROKEN  ({o.var_name} -> {o.env_path})")
        lines.append(f"  Run `{FIX_PHRASE}` to clean up.")

    total = len(findings)
    lines.append("")
    summary = f"Found {total} keys: {protected_count} protected, {unprotected_count} unprotected"
    if orphans:
        summary += f", {len(orphans)} broken"
    lines.append(summary)

    if unprotected_count > 0:
        if is_tty:
            lines.append("Run: worthless lock")
        else:
            lines.append("See: docs.worthless.dev/ci-setup")

    return "\n".join(lines) + "\n"


def _format_json_findings(findings: list[ScanFinding], orphans: list | None = None) -> str:
    """Format findings as JSON. HF5: shape changed from bare array to
    ``{"findings": [...], "orphans": [...]}`` so we can carry the broken
    DB rows alongside the .env findings. JSON consumers iterating
    findings need to switch from ``for f in result`` to
    ``for f in result["findings"]`` — documented in SKILL.md.
    """
    items = []
    for f in findings:
        items.append(
            {
                "file": f.file,
                "line": f.line,
                "var_name": f.var_name,
                "provider": f.provider,
                "is_protected": f.is_protected,
                "value_preview": f.value_preview,
            }
        )
    orphan_items = [
        {
            "alias": o.key_alias,
            "var_name": o.var_name,
            "env_path": o.env_path,
        }
        for o in (orphans or [])
    ]
    return (
        json.dumps(
            {"schema_version": 2, "findings": items, "orphans": orphan_items},
            indent=2,
        )
        + "\n"
    )


def _install_hook() -> None:
    """Write or append worthless scan to .git/hooks/pre-commit."""
    git_dir = _find_git_dir()
    if git_dir is None:
        raise WorthlessError(ErrorCode.SCAN_ERROR, "No .git directory found", exit_code=2)

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


async def _load_db_state_async():
    """Return ``(enrolled_locations, orphans)`` from the worthless DB.

    HF5 / worthless-gmky: scan now needs BOTH the (var_name, env_path) set
    used to mark findings as PROTECTED, AND the list of orphan enrollments
    (DB rows whose ``.env`` line is gone) so it can surface them in a
    dedicated section. ``find_orphans`` from ``cli/orphans.py`` is the
    shared predicate (HF7).

    Returns ``(None, [])`` when the DB is unavailable — graceful
    degradation in CI / first-scan / no-home contexts.
    """
    try:
        home = get_home()
    except Exception:
        return None, []

    if not home.db_path.exists():
        return None, []

    try:
        # HF3 (worthless-cmpf): placeholder Fernet — list_enrollments only
        # reads plaintext metadata, no decrypt path triggered, no keychain
        # prompt for this read-only command. Contract pinned in
        # tests/test_scan_no_keystore.py.
        placeholder_fernet = bytearray(PLACEHOLDER_FERNET_KEY)
        repo = ShardRepository(str(home.db_path), placeholder_fernet)
        await repo.initialize()
        enrollments = await repo.list_enrollments()
    except Exception:
        return None, []

    if not enrollments:
        return None, []

    return build_enrolled_locations(enrollments), find_orphans(enrollments)


def _load_db_state():
    """Sync wrapper for CLI (typer) context. Returns (enrolled, orphans)."""
    try:
        return asyncio.run(_load_db_state_async())
    except Exception:
        return None, []


_HONESTY_FOOTER = (
    "NOTE — this scan catches LITERAL URLs from the bundled registry.\n"
    "It does NOT detect: runtime-composed URLs, IP literals, regional/\n"
    "Azure/Bedrock endpoints, env-var interpolation, or vendored SDKs.\n"
)


def _format_code_findings_human(findings: list[CodeFinding]) -> str:
    """Render code findings + honesty footer for stderr output."""
    if not findings:
        return "No hardcoded provider URLs found.\n"

    lines: list[str] = []
    for f in findings:
        lines.append(
            f"[code] {f.file}:{f.line}:{f.column}  {f.provider_name} ({f.suggested_env_var})"
        )
        lines.append(f"       {f.matched_url}")
        # Show the offending source line (trimmed so it doesn't blow up the
        # terminal). The user's eyes go straight to the arrow + line.
        snippet = f.line_text.strip()
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        lines.append(f"       → {snippet}")
        lines.append("")

    lines.append(f"Found {len(findings)} hardcoded provider URL(s).")
    lines.append("")
    lines.append(_HONESTY_FOOTER)
    return "\n".join(lines)


def _format_ai_prompt_block(findings: list[CodeFinding]) -> str:
    """Copy-pasteable prompt the user hands to whatever AI agent they're
    running (Claude Code, Cursor, etc.). One bullet per finding."""
    if not findings:
        return ""

    sep = "─" * 68
    bullets = []
    for f in findings:
        bullets.append(
            f"- {f.file}:{f.line}  → use environment variable "
            f"{f.suggested_env_var} (default to {f.matched_url!r} when unset)"
        )

    return (
        f"\n{sep}\n"
        "COPY THIS TO YOUR AI AGENT (Claude Code, Cursor, etc.):\n"
        f"{sep}\n"
        "The following files contain hardcoded LLM provider URLs that "
        "should be read from environment variables so `worthless` can "
        "proxy the traffic. For each location, replace the literal URL "
        "with the suggested env var, defaulting to the same URL when "
        "the var is unset.\n\n" + "\n".join(bullets) + "\n\n"
        "Preserve quoting, indentation, and existing comments. Do not "
        "modify files under .venv/, node_modules/, vendor/, dist/, or "
        "any other dependency directory.\n"
        f"{sep}\n"
    )


def _code_findings_to_json(findings: list[CodeFinding]) -> list[dict[str, object]]:
    """Serialize code findings for JSON output."""
    return [
        {
            "file": f.file,
            "line": f.line,
            "column": f.column,
            "matched_url": f.matched_url,
            "provider_name": f.provider_name,
            "suggested_env_var": f.suggested_env_var,
            "line_text": f.line_text,
        }
        for f in findings
    ]


def register_scan_commands(app: typer.Typer) -> None:
    """Register the scan command on the Typer app."""

    @app.command()
    @error_boundary(exit_code=2)
    def scan(
        paths: list[Path] | None = typer.Argument(
            None,
            help="Files to scan",
        ),
        deep: bool = typer.Option(
            False,
            "--deep",
            help="Extended scan (env vars, config files)",
        ),
        pre_commit: bool = typer.Option(
            False,
            "--pre-commit",
            help="Pre-commit hook mode",
        ),
        format_: str = typer.Option(
            "text",
            "--format",
            "-f",
            help="Output format: text, sarif, json",
            show_choices=True,
        ),
        show_suffix: bool = typer.Option(
            False,
            "--show-suffix",
            help="Show last 4 chars of keys",
        ),
        install_hook: bool = typer.Option(
            False,
            "--install-hook",
            help="Install git pre-commit hook",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Output JSON (alias for --format json)",
        ),
        code: bool = typer.Option(
            False,
            "--code",
            help=(
                "Also scan project source for hardcoded LLM provider URLs "
                "(worthless-7sl9). Warn-only — never changes exit code."
            ),
        ),
        ai_prompt: bool = typer.Option(
            True,
            "--ai-prompt/--no-ai-prompt",
            help=(
                "When --code findings exist, append a copy-pasteable prompt "
                "block for an AI agent (Claude Code, Cursor, ...). On by default."
            ),
        ),
    ) -> None:
        """Detect exposed API keys in files and environment."""
        console = get_console()

        # Handle --install-hook
        if install_hook:
            _install_hook()
            console.print_success("Pre-commit hook installed.")
            raise typer.Exit(code=0)

        # Resolve format
        fmt = format_
        if json_output:
            fmt = "json"
        if fmt not in ("text", "sarif", "json"):
            raise WorthlessError(
                ErrorCode.SCAN_ERROR,
                f"Unknown format: {fmt!r} (use text, sarif, or json)",
                exit_code=2,
            )

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

            # Build enrollment checker + orphan list from DB if available
            enrolled, orphans = _load_db_state()

            # Run scan
            findings = scan_files(scan_paths, enrolled_locations=enrolled)

            # Count unprotected
            unprotected = [f for f in findings if not f.is_protected]

            # Run --code scan if requested (worthless-7sl9). Always
            # warn-only — never modifies exit code. Independent of the
            # .env scan above.
            code_findings: list[CodeFinding] = []
            if code:
                code_roots = explicit if explicit else [Path.cwd()]
                # Guard: explicit paths must exist. Without this, a typo
                # like ``scan --code /does/not/exist`` would silently
                # report "no findings" — a worse UX than a clear error.
                for p in code_roots:
                    if not p.exists():
                        raise WorthlessError(
                            ErrorCode.SCAN_ERROR,
                            f"Path not found: {p}",
                            exit_code=2,
                        )
                # WSL /mnt/ paths cross the Windows filesystem boundary;
                # stat(2) runs at 5-15 ms each instead of ~5 µs on native
                # filesystems. Warn early so a multi-second scan doesn't
                # look like a hang.
                if not console.quiet and fmt not in ("json", "sarif"):
                    if any(str(p.resolve()).startswith("/mnt/") for p in code_roots):
                        sys.stderr.write(
                            "Note: scanning on WSL /mnt/ — stat(2) crosses the "
                            "Windows filesystem boundary; large repos may take "
                            "several seconds.\n"
                        )
                        sys.stderr.flush()
                code_findings = scan_for_hardcoded_provider_urls(code_roots)

            # Output
            if fmt == "sarif":
                sarif = format_sarif(findings, "0.1.0")
                sys.stdout.write(json.dumps(sarif, indent=2) + "\n")
                sys.stdout.flush()
            elif fmt == "json":
                # Merge code_findings into the existing JSON envelope.
                payload = json.loads(_format_json_findings(findings, orphans))
                if code:
                    payload["code_findings"] = _code_findings_to_json(code_findings)
                sys.stdout.write(json.dumps(payload) + "\n")
                sys.stdout.flush()
            else:
                # Human-readable to stderr
                is_tty = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
                text = _format_human(
                    findings, orphans=orphans, show_suffix=show_suffix, is_tty=is_tty
                )
                if not console.quiet:
                    sys.stderr.write(text)
                    if code:
                        sys.stderr.write(_format_code_findings_human(code_findings))
                        if ai_prompt and code_findings:
                            sys.stderr.write(_format_ai_prompt_block(code_findings))
                    sys.stderr.flush()

            # Exit code — unchanged by --code (warn-only contract).
            if unprotected:
                raise typer.Exit(code=1)
            raise typer.Exit(code=0)
        finally:
            if tmp_file is not None:
                tmp_file.unlink(missing_ok=True)
