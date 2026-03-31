"""Key pattern detection with entropy and decoy awareness."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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


def scan_files(
    paths: list[Path],
    is_decoy: Callable[[str], bool] | None = None,
) -> list[ScanFinding]:
    """Scan files for API key patterns.

    Each file is read line-by-line. Matches with entropy below the
    threshold are skipped (likely placeholders). If *is_decoy* is
    provided, matching values are marked ``is_protected=True``.
    """
    findings: list[ScanFinding] = []

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

                is_protected = bool(is_decoy and is_decoy(value))

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
