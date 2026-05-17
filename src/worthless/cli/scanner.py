"""Key pattern detection with entropy and enrollment awareness."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from worthless.cli.dotenv_rewriter import shannon_entropy
from worthless.cli.key_patterns import ENTROPY_THRESHOLD, KEY_PATTERN, detect_provider

_VAR_NAME_RE = re.compile(r"(\w+)\s*$")

# Source file extensions to scan for hardcoded provider URLs.
_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rb",
        ".java",
        ".cs",
        ".rs",
        ".php",
        ".kt",
        ".swift",
    }
)

# Directory names that are never user code — skip them entirely.
_NOISE_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        ".eggs",
        "htmlcov",
        ".ruff_cache",
        ".next",
        ".nuxt",
        "target",
        "vendor",
    }
)

# Provider hostnames that route to localhost are already local — not a bypass.
_LOCAL_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

# Backreference ensures closing quote matches opening — prevents mismatched-quote false positives.
# Group 1 = quote char, group 2 = content between the matched quotes.
_QUOTED_STR_RE = re.compile(r"""(['"])([^"'\n]+)\1""")


@dataclass
class HardcodedUrlFinding:
    """A provider URL hardcoded in a source file — potential proxy bypass."""

    file: str
    line: int
    url: str
    provider: str


def scan_source_for_hardcoded_provider_urls(
    project_root: Path,
) -> list[HardcodedUrlFinding]:
    """Walk source files under *project_root*, return any hardcoded provider URLs.

    Uses the bundled provider registry so the list of flagged hostnames stays
    in sync with what worthless actually knows about. Localhost providers
    (e.g. Ollama on 127.0.0.1) are excluded — they're already local.
    """
    from worthless.cli.providers import load_bundled  # deferred — avoids circular at module init

    registry = load_bundled()
    hostnames: dict[str, str] = {}  # hostname → provider name
    for entry in registry.values():
        hostname = urlparse(entry.url).hostname or ""
        if hostname and hostname not in _LOCAL_HOSTNAMES:
            hostnames[hostname] = entry.name

    if not hostnames:
        return []

    combined = re.compile("|".join(re.escape(h) for h in hostnames))
    findings: list[HardcodedUrlFinding] = []
    for src_file in _walk_source_files(project_root):
        try:
            with src_file.open(errors="replace") as fh:
                for line_no, line in enumerate(fh, start=1):
                    for m in _QUOTED_STR_RE.finditer(line):
                        value = m.group(2)  # group 1 is the quote char; group 2 is content
                        host_match = combined.search(value)
                        if host_match:
                            findings.append(
                                HardcodedUrlFinding(
                                    file=str(src_file),
                                    line=line_no,
                                    url=value,
                                    provider=hostnames[host_match.group(0)],
                                )
                            )
        except OSError:
            continue
    return findings


def _walk_source_files(root: Path) -> Iterator[Path]:
    """Yield source files under *root*, pruning noise directories without descending into them."""
    stack = [root]
    while stack:
        try:
            entries = list(stack.pop().iterdir())
        except OSError:
            continue
        for item in entries:
            if item.is_dir() and not item.is_symlink():
                if item.name not in _NOISE_DIRS and not item.name.endswith(".egg-info"):
                    stack.append(item)
            elif item.suffix in _SOURCE_EXTENSIONS:
                yield item


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
    *,
    enrolled_locations: set[tuple[str, str]] | None = None,
) -> list[ScanFinding]:
    """Scan files for API key patterns.

    Each file is read line-by-line. Matches with entropy below the
    threshold are skipped (likely placeholders). If *enrolled_locations*
    is provided, matching (var_name, file_path) tuples are marked
    ``is_protected=True``.
    """
    findings: list[ScanFinding] = []

    for path in paths:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        file_str = str(path.resolve())
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

                is_protected = bool(
                    enrolled_locations and var_name and (var_name, file_str) in enrolled_locations
                )

                findings.append(
                    ScanFinding(
                        file=str(path),
                        line=line_no,
                        var_name=var_name,
                        provider=provider,
                        is_protected=is_protected,
                        value_preview=_mask(value),
                    )
                )
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
                                "shortDescription": {"text": "Exposed API key detected"},
                            }
                        ],
                    }
                },
                "results": results,
            }
        ],
    }
