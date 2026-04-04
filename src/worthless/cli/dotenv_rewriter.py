"""Atomic prefix-preserving .env key replacement and scanning."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from dotenv import dotenv_values, set_key

from worthless.cli.key_patterns import ENTROPY_THRESHOLD, KEY_PATTERN, detect_provider


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of string *s* in bits."""
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


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
    parsed = dotenv_values(env_path)
    for var_name, value in parsed.items():
        if value is None:
            continue
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
    Uses python-dotenv's ``set_key`` which handles multiline values,
    export prefixes, and quoted strings correctly.
    Raises ``KeyError`` if *var_name* is not found.
    """
    # Verify the key exists before writing — set_key would silently add it.
    existing = dotenv_values(env_path)
    if var_name not in existing:
        raise KeyError(f"Variable {var_name!r} not found in {env_path}")

    set_key(str(env_path), var_name, new_value, quote_mode="never")
