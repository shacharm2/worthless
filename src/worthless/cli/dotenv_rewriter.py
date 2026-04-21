"""Atomic .env key replacement and scanning via python-dotenv."""

from __future__ import annotations

import math
import os
import stat
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values, set_key, unset_key

from worthless.cli.key_patterns import ENTROPY_THRESHOLD, KEY_PATTERN, detect_provider

if TYPE_CHECKING:
    from worthless.storage.repository import EnrollmentRecord


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of string *s* in bits."""
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def build_enrolled_locations(
    enrollments: Iterable[EnrollmentRecord],
) -> set[tuple[str, str]]:
    """Build a set of ``(var_name, env_path)`` from enrollment records.

    Entries with ``env_path=None`` (direct enrollments) are excluded.
    """
    return {(e.var_name, e.env_path) for e in enrollments if e.env_path}


def scan_env_keys(
    env_path: Path,
    *,
    enrolled_locations: set[tuple[str, str]] | None = None,
) -> list[tuple[str, str, str]]:
    """Find API keys in a ``.env`` file.

    Returns a list of ``(var_name, value, provider)`` tuples for lines
    whose value matches a known provider prefix and is not a low-entropy
    placeholder.

    Parameters
    ----------
    enrolled_locations:
        Optional set of ``(var_name, env_path)`` tuples that are already
        enrolled.  Matching entries are skipped.
    """
    results: list[tuple[str, str, str]] = []
    parsed = dotenv_values(env_path)
    env_str = str(env_path.resolve())
    for var_name, value in parsed.items():
        if value is None:
            continue
        if not KEY_PATTERN.search(value):
            continue
        if enrolled_locations and (var_name, env_str) in enrolled_locations:
            continue
        if shannon_entropy(value) < ENTROPY_THRESHOLD:
            continue
        provider = detect_provider(value)
        if provider:
            results.append((var_name, value, provider))
    return results


def _check_env_path_safe(path: Path) -> None:
    """Reject symlinks, FIFOs, block devices, and hardlinked files."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    mode = st.st_mode
    if stat.S_ISLNK(mode) or stat.S_ISFIFO(mode) or stat.S_ISBLK(mode):
        raise OSError(f"Refusing to write to non-regular file: {path}")
    if st.st_nlink > 1:
        raise OSError(f"Refusing to write to hardlinked file: {path}")


def _fsync_path(path: Path) -> None:
    """fsync the file at *path*; swallow OSError."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _fsync_dir(path: Path) -> None:
    """fsync the parent directory of *path*; swallow OSError (Windows)."""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def add_or_rewrite_env_key(env_path: Path, var_name: str, value: str) -> None:
    """Set *var_name* to *value* in *env_path*, creating or updating.

    If *var_name* already exists, its value is replaced in place.
    If it does not exist, a new line is appended.
    """
    _check_env_path_safe(env_path)
    set_key(str(env_path), var_name, value, quote_mode="never")
    _fsync_path(env_path)
    _fsync_dir(env_path)


def remove_env_key(env_path: Path, var_name: str) -> None:
    """Remove *var_name* from *env_path* if it exists.

    Uses python-dotenv's ``unset_key`` which handles quoted values,
    export prefixes, and multiline entries correctly.
    Silently no-ops if the variable is not present.
    """
    unset_key(str(env_path), var_name)


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

    _check_env_path_safe(env_path)
    set_key(str(env_path), var_name, new_value, quote_mode="never")
    _fsync_path(env_path)
    _fsync_dir(env_path)
