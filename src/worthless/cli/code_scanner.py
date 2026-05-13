"""Hardcoded-provider-URL code scanner (worthless-7sl9).

Detects literal LLM provider base URLs (drawn from ``providers.toml``)
embedded in project source. The scan is opt-in via ``worthless scan
--code`` and is warn-only — it never fails the command. The CLI emits
a copy-pasteable AI-agent prompt block so the user can hand the fix
to whatever agent they already have running (Claude Code, Cursor, etc.).

Lock-side coupling is explicitly deferred (WOR-493 / worthless-8a5d).

Output surfaces:
- text (default) — human-readable; AI prompt block appended when findings exist
- json (``--json``) — adds top-level ``code_findings`` array
- sarif (``--format sarif``) — **omits code findings by design**; SARIF
  output remains the .env-key surface only. Adding a hardcoded-provider-url
  SARIF rule is an additive future change, not a current gap.
"""

from __future__ import annotations

import logging
import os
import stat as _stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable

from worthless.cli.key_patterns import KEY_PATTERN
from worthless.cli.providers import ProviderEntry, load_registry

logger = logging.getLogger(__name__)

# File-type allowlist. Anything outside this set is skipped — most user
# repos contain build artifacts, binaries, and locale files that would
# otherwise produce noise.
_SCANNED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".swift",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".md",
    }
)

# Directory names anywhere in the path → exclude.
_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "target",  # Rust
        "site-packages",
        ".next",
        ".nuxt",
    }
)

# File suffixes that get skipped even when the extension is allowed.
_EXCLUDED_FILE_SUFFIXES: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".lock",
    ".lockfile",
)

_EXCLUDED_FILE_BASENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "providers.toml",  # source-of-truth registry — scanning it is always a false positive
    }
)

# Files larger than this are skipped to avoid pathological perf on
# generated/vendored bundles.
_MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class CodeFinding:
    """A single hardcoded provider URL detected in project source.

    All positions are 1-indexed (line + column) to match how editors
    display them.
    """

    file: str
    line: int
    column: int
    matched_url: str
    provider_name: str
    suggested_env_var: str
    line_text: str


def _is_excluded_path(path: Path, excluded_dirs: frozenset[str]) -> bool:
    """True if any path component is in the directory excludelist or the
    basename matches an excluded file pattern."""
    if not excluded_dirs.isdisjoint(path.parts):
        return True

    name = path.name
    if name in _EXCLUDED_FILE_BASENAMES:
        return True
    return name.endswith(_EXCLUDED_FILE_SUFFIXES)


def _list_files_git(root: Path) -> list[Path] | None:
    """Return tracked + untracked-but-not-ignored files via ``git ls-files``.

    Returns ``None`` if ``root`` is not inside a git working tree (caller
    falls back to a filesystem walk).
    """
    try:
        # ``git`` from PATH is intentional — pinning to /usr/bin/git breaks
        # Windows/WSL (the target-user happy path per project_target_users).
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    files: list[Path] = []
    for rel in result.stdout.splitlines():
        if not rel:
            continue
        f = root / rel
        if f.is_symlink():
            continue
        files.append(f)
    return files


def _list_files_walk(root: Path, excluded_dirs: frozenset[str] = _EXCLUDED_DIRS) -> list[Path]:
    """Recursive filesystem walk that does not follow symlinks and prunes
    excluded directories in-place."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # In-place mutation prunes the descent — must be a list assignment.
        dirnames[:] = [d for d in dirnames if d not in excluded_dirs]
        for name in filenames:
            files.append(Path(dirpath) / name)
    return files


def _candidate_files(root: Path, excluded_dirs: frozenset[str] = _EXCLUDED_DIRS) -> list[Path]:
    """All files under ``root`` that should be scanned. Honors .gitignore
    when ``root`` is a git working tree."""
    if not root.exists():
        return []

    git_files = _list_files_git(root) if root.is_dir() else None
    files = (
        git_files
        if git_files is not None
        else (_list_files_walk(root, excluded_dirs) if root.is_dir() else [root])
    )

    candidates: list[Path] = []
    for f in files:
        # Cheap path-based checks first (no syscall), then a single stat(2)
        # that covers both the is-regular-file and size guard. OSError on
        # any of these means the file vanished or is unreadable — skip it.
        try:
            # Check absolute parts so excludes living above ``root`` (e.g.
            # scanning a project inside node_modules/) still prune.
            if _is_excluded_path(f, excluded_dirs):
                continue
            if f.suffix.lower() not in _SCANNED_EXTENSIONS:
                continue
            st = f.stat()
            if not _stat.S_ISREG(st.st_mode):
                continue
            if st.st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        candidates.append(f)
    return candidates


def _scan_one_file(
    path: Path,
    registry_lower: dict[str, ProviderEntry],
) -> list[CodeFinding]:
    """Return findings for a single file, or [] on any I/O / decode error."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("code_scanner: skipping %s (%s)", path, exc)
        return []

    text_lower = text.casefold()
    if not any(url in text_lower for url in registry_lower):
        return []

    findings: list[CodeFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line_lower = line.casefold()
        for url_lower, entry in registry_lower.items():
            idx = line_lower.find(url_lower)
            if idx < 0:
                continue
            findings.append(
                CodeFinding(
                    file=str(path),
                    line=lineno,
                    column=idx + 1,
                    matched_url=entry.url,
                    provider_name=entry.name,
                    suggested_env_var=f"{entry.name.upper()}_BASE_URL",
                    line_text=KEY_PATTERN.sub("[REDACTED]", line),
                )
            )
    return findings


def scan_for_hardcoded_provider_urls(
    roots: Iterable[Path],
    *,
    extra_excludes: Iterable[str] = (),
) -> list[CodeFinding]:
    """Scan one or more roots for hardcoded provider base URLs.

    Each entry in ``providers.toml`` (via ``load_registry()``) becomes a
    case-insensitive substring matcher applied line-by-line to every
    candidate source file under each root.

    Args:
        roots: directories (or single files) to scan.
        extra_excludes: directory names to add to the built-in excludelist.

    Returns:
        Findings in (file, line) order across all roots. Empty when no
        provider registry is configured or no candidates match.
    """
    registry = load_registry()
    if not registry:
        return []

    # registry maps url → ProviderEntry. Index by case-folded url for
    # case-insensitive matching while preserving the canonical entry.
    registry_lower: dict[str, ProviderEntry] = {
        url.casefold(): entry for url, entry in registry.items()
    }

    # extra_excludes flows into the dir excludelist for this call only.
    extras = frozenset(extra_excludes)
    excluded_dirs = _EXCLUDED_DIRS | extras

    findings: list[CodeFinding] = []
    for root in roots:
        # Resolve to absolute so part-based exclude checks are stable.
        root_path = Path(root).resolve()
        for candidate in _candidate_files(root_path, excluded_dirs):
            findings.extend(_scan_one_file(candidate, registry_lower))

    # Stable ordering: by file, then line, then column.
    findings.sort(key=lambda f: (f.file, f.line, f.column))
    return findings
