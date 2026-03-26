"""Key pattern detection with entropy and decoy awareness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from worthless.cli.dotenv_rewriter import shannon_entropy
from worthless.cli.key_patterns import KEY_PATTERN, detect_provider

_ENTROPY_THRESHOLD = 4.5


@dataclass
class ScanFinding:
    """A detected API key occurrence in a scanned file."""

    file: str
    line: int
    var_name: str | None
    provider: str
    is_protected: bool
    value_preview: str  # fully masked by default


def load_enrollment_data(home: object | None) -> set[str]:
    """Read shard_a files to get known decoy values.

    Returns an empty set if *home* is ``None`` (CI mode) or if no
    shard_a directory exists.
    """
    if home is None:
        return set()
    shard_a_dir = getattr(home, "shard_a_dir", None)
    if shard_a_dir is None or not Path(shard_a_dir).exists():
        return set()
    values: set[str] = set()
    for f in Path(shard_a_dir).iterdir():
        if f.is_file():
            values.add(f.read_text().strip())
    return values


def scan_files(
    paths: list[Path],
    enrollment_data: set[str] | None = None,
) -> list[ScanFinding]:
    """Scan files for API key patterns.

    Each file is read line-by-line. Matches with entropy below the
    threshold are skipped (likely placeholders). If *enrollment_data*
    is provided and contains the matched value, the finding is marked
    ``is_protected=True``.
    """
    findings: list[ScanFinding] = []
    enrolled = enrollment_data or set()

    for path in paths:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in KEY_PATTERN.finditer(line):
                value = match.group(0)
                if shannon_entropy(value) < _ENTROPY_THRESHOLD:
                    continue
                provider = detect_provider(value)
                if provider is None:
                    continue

                # Try to extract var_name from KEY=VALUE or KEY = "VALUE"
                var_name = _extract_var_name(line, match.start())

                is_protected = value in enrolled

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
        # Take the last word-like token
        import re
        m = re.search(r'(\w+)\s*$', prefix)
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
