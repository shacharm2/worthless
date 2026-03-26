"""Atomic prefix-preserving .env key replacement and scanning."""

from __future__ import annotations

import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

from worthless.cli.key_patterns import KEY_PATTERN, detect_provider

_ENTROPY_THRESHOLD = 4.5


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of string *s* in bits."""
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in counts.values()
    )


def scan_env_keys(env_path: Path) -> list[tuple[str, str, str]]:
    """Find API keys in a ``.env`` file.

    Returns a list of ``(var_name, value, provider)`` tuples for lines
    whose value matches a known provider prefix and has entropy above
    the threshold (filtering out placeholders).
    """
    results: list[tuple[str, str, str]] = []
    text = env_path.read_text()
    for line in text.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if "=" not in line_stripped:
            continue
        var_name, _, raw_value = line_stripped.partition("=")
        var_name = var_name.strip()
        value = raw_value.strip().strip("\"'")
        if not KEY_PATTERN.search(value):
            continue
        if shannon_entropy(value) < _ENTROPY_THRESHOLD:
            continue
        provider = detect_provider(value)
        if provider:
            results.append((var_name, value, provider))
    return results


def rewrite_env_key(env_path: Path, var_name: str, new_value: str) -> None:
    """Atomically replace the value of *var_name* in *env_path*.

    Preserves comments, blank lines, ordering, and all other variables.
    Raises ``KeyError`` if *var_name* is not found.
    """
    text = env_path.read_text()
    lines = text.splitlines(keepends=True)
    found = False
    new_lines: list[str] = []

    pattern = re.compile(rf"^{re.escape(var_name)}\s*=")

    for line in lines:
        if pattern.match(line.lstrip()):
            # Preserve any trailing newline from the original line
            eol = "\n" if line.endswith("\n") else ""
            new_lines.append(f"{var_name}={new_value}{eol}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        raise KeyError(f"Variable {var_name!r} not found in {env_path}")

    # Atomic write: write to temp file, then os.replace
    dir_path = env_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), prefix=".env.tmp.")
    try:
        os.write(fd, "".join(new_lines).encode())
        os.close(fd)
        os.replace(tmp_path, str(env_path))
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
