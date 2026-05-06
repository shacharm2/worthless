"""Canonical openclaw.json config reader/writer.

The single entry point for reading and mutating the OpenClaw provider
configuration file (``openclaw.json``) shared between the CLI and the
sidecar. Both must go through this module so on-disk schema stays
consistent and writes stay atomic across concurrent processes.

Schema (subset we touch)::

    {
      "models": {
        "providers": {
          "<provider_name>": {
            "baseUrl": "...",
            "apiKey": "...",
            "api": "openai-completions" | "anthropic-messages" | ...,
            "models": [{"id": "...", "name": "..."}]
          }
        }
      }
    }

Writes are atomic (tempfile + ``os.replace``) and serialized via
inter-process ``flock`` to prevent lost-update races between concurrent
read-modify-write transactions.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class OpenclawConfigError(Exception):
    """Raised when openclaw.json is unreadable or malformed."""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _global_config_candidates() -> list[Path]:
    """Return the canonical global openclaw.json search paths, in priority order.

    Verified live (2026-05): the OpenClaw daemon container reads
    ``/home/node/.openclaw/openclaw.json``. On host platforms OpenClaw
    likewise stores config under ``~/.openclaw/`` — same on macOS and Linux,
    contrary to platform-conventional ``Library/Application Support`` /
    ``XDG_CONFIG_HOME`` paths.

    We probe ``~/.openclaw/openclaw.json`` first, then fall back to
    ``~/.config/openclaw/openclaw.json`` (XDG) for users who set OpenClaw up
    via a non-default path. macOS ``Library/Application Support`` is **not**
    probed: OpenClaw doesn't write there.
    """
    home = Path("~").expanduser()
    return [
        home / ".openclaw" / "openclaw.json",
        home / ".config" / "openclaw" / "openclaw.json",
    ]


def locate_config_path() -> Path | None:
    """Locate the active openclaw.json.

    Resolution order:

    1. ``./openclaw.json`` in the current working directory (project-local).
    2. ``~/.openclaw/openclaw.json`` (canonical global path used by the
       OpenClaw daemon).
    3. ``~/.config/openclaw/openclaw.json`` (XDG fallback for non-default
       installs).

    Returns the first existing path, or ``None`` if none exist.
    """
    local = Path.cwd() / "openclaw.json"
    if local.exists():
        return local

    for candidate in _global_config_candidates():
        if candidate.exists():
            return candidate

    return None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_config(path: Path) -> dict[str, Any]:
    """Read and parse openclaw.json.

    Returns ``{}`` if the file does not exist. Raises
    :class:`OpenclawConfigError` if the file exists but is not valid JSON
    (or is not a JSON object).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise OpenclawConfigError(f"could not read {path}: {exc}") from exc

    if not raw.strip():
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenclawConfigError(f"malformed JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise OpenclawConfigError(
            f"expected JSON object at top level of {path}, got {type(data).__name__}"
        )

    return data


def _providers_view(data: dict[str, Any], *, strict: bool) -> dict[str, Any] | None:
    """Return ``data['models']['providers']`` if present and well-typed, else ``None``.

    ``strict=True``: raise :class:`OpenclawConfigError` on malformed shape.
    ``strict=False``: return ``None`` (callers treat this as "no providers").
    """
    models = data.get("models")
    if models is None:
        return None
    if not isinstance(models, dict):
        if strict:
            raise OpenclawConfigError("'models' must be a JSON object")
        return None
    providers = models.get("providers")
    if providers is None:
        return None
    if not isinstance(providers, dict):
        if strict:
            raise OpenclawConfigError("'models.providers' must be a JSON object")
        return None
    return providers


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    """Acquire an exclusive ``flock`` for the duration of a read-modify-write.

    ``os.replace`` guarantees no torn file but does NOT prevent lost updates
    when multiple processes do concurrent read-modify-write on the same file.
    Without this lock, the WOR-321 sidecar and the WOR-431 CLI could write
    simultaneously and silently drop providers.

    The lock file is a sibling sentinel ``.<name>.lock`` in the same
    directory as the target. We hold an exclusive ``flock`` for the whole
    R-M-W transaction (read → mutate → ``os.replace``).

    Unix-only: ``worthless`` refuses native Windows (WRTLS-110); WSL works
    because it's Linux to ``fcntl``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{target.name}.lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``path`` atomically.

    The serialization happens to a tempfile in the same directory, then
    ``os.replace`` swaps it into place. A crash mid-write leaves the
    pre-existing file untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"

    tmp_path: Path | None = None
    fd: int | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        # ``os.replace`` (not ``Path.replace``) is patchable at the module
        # level so the atomic-write contract test can simulate disk-full.
        os.replace(tmp_path, path)  # noqa: PTH105
        tmp_path = None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _ensure_providers(data: dict[str, Any]) -> dict[str, Any]:
    """Return ``data['models']['providers']``, creating the path if missing."""
    existing = _providers_view(data, strict=True)
    if existing is not None:
        return existing
    models = data.setdefault("models", {})
    return models.setdefault("providers", {})


# ---------------------------------------------------------------------------
# Public mutators
# ---------------------------------------------------------------------------


def set_provider(
    path: Path,
    provider: str,
    base_url: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Idempotently set ``models.providers.<provider>.baseUrl``.

    If ``api_key`` is supplied it is also written. Other fields on the
    provider entry (e.g. ``api``, ``models``) are preserved. Other
    providers are left untouched.

    Creates the file (and any missing parent directories) when absent.

    Returns the resulting provider entry. The whole read-modify-write is
    serialized via an inter-process flock to prevent lost updates between
    concurrent CLI and sidecar writers.
    """
    with _file_lock(path):
        data = read_config(path)
        providers = _ensure_providers(data)

        entry: dict[str, Any] = dict(providers.get(provider) or {})
        entry["baseUrl"] = base_url
        if api_key is not None:
            entry["apiKey"] = api_key

        providers[provider] = entry
        _atomic_write_json(path, data)
        return entry


def unset_provider(path: Path, provider: str) -> dict[str, Any]:
    """Remove ``models.providers.<provider>`` entirely.

    Returns the removed entry as a dict, or ``{}`` if it was not present.
    Other providers are left untouched. The file is rewritten atomically,
    and the read-modify-write is serialized via an inter-process flock.
    """
    with _file_lock(path):
        data = read_config(path)
        providers = _providers_view(data, strict=False) if data else None
        if providers is None or provider not in providers:
            return {}

        removed = providers.pop(provider)
        _atomic_write_json(path, data)
        return removed if isinstance(removed, dict) else {}


def get_provider(path: Path, provider: str) -> dict[str, Any] | None:
    """Return ``models.providers.<provider>`` or ``None`` if absent."""
    data = read_config(path)
    providers = _providers_view(data, strict=False) if data else None
    if providers is None:
        return None

    entry = providers.get(provider)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise OpenclawConfigError(f"provider '{provider}' is not a JSON object in {path}")
    return copy.deepcopy(entry)
