"""Atomic prefix-preserving .env key replacement and scanning."""

from __future__ import annotations

import math
import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from worthless.cli.key_patterns import ENTROPY_THRESHOLD, KEY_PATTERN, detect_provider


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


def scan_env_keys(
    env_path: Path,
    is_decoy: Callable[[str], bool] | None = None,
) -> list[tuple[str, str, str]]:
    """Find API keys in a ``.env`` file.

    Returns a list of ``(var_name, value, provider)`` tuples for lines
    whose value matches a known provider prefix and is not a known decoy
    or low-entropy placeholder.

    Parameters
    ----------
    is_decoy:
        Optional predicate that returns True for values that are known
        decoys (checked via hash registry).  When provided, matching
        values are skipped before the entropy check.
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
        if is_decoy and is_decoy(value):
            continue
        if shannon_entropy(value) < ENTROPY_THRESHOLD:
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
    fd_closed = False
    try:
        os.write(fd, "".join(new_lines).encode())
        os.close(fd)
        fd_closed = True
        Path(tmp_path).replace(env_path)
    except BaseException:
        if not fd_closed:
            os.close(fd)
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
