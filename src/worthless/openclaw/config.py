"""Canonical openclaw.json config reader/writer.

This module is the single source of truth for parsing and mutating the
OpenClaw provider configuration file (``openclaw.json``). It is imported
by both:

* WOR-431 — the ``worthless openclaw enable/disable/status`` CLI verbs that
  let humans flip OpenClaw between Worthless-proxied and direct mode.
* WOR-321 — the sidecar's auto-configuration of the OpenClaw container,
  which writes the same file at the container bind-mount path
  ``/home/node/.openclaw/openclaw.json`` so OpenClaw routes through the
  Worthless proxy on first boot.

Both consumers MUST go through the public API in this module so that the
on-disk schema stays consistent and writes stay atomic.

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

Writes are atomic: we serialize to a tempfile in the same directory as the
target, then ``os.replace`` it into place. A failure mid-write leaves the
existing file untouched.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


class OpenclawConfigError(Exception):
    """Raised when openclaw.json is unreadable or malformed."""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _global_config_path() -> Path:
    """Return the platform-appropriate global openclaw.json path."""
    home = Path("~").expanduser()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "openclaw" / "openclaw.json"
    # Linux + everything else uses XDG-style ~/.config.
    return home / ".config" / "openclaw" / "openclaw.json"


def locate_config_path() -> Path | None:
    """Locate the active openclaw.json.

    Resolution order:

    1. ``./openclaw.json`` in the current working directory (project-local).
    2. The platform global path (``~/.config/openclaw/openclaw.json`` on
       Linux, ``~/Library/Application Support/openclaw/openclaw.json`` on
       macOS).

    Returns the first existing path, or ``None`` if neither exists.
    """
    local = Path.cwd() / "openclaw.json"
    if local.exists():
        return local

    global_path = _global_config_path()
    if global_path.exists():
        return global_path

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
    if not path.exists():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


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
            fd = None  # fdopen took ownership
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        # Use ``os.replace`` (not ``Path.replace``) so tests can patch the
        # module-level ``os`` symbol to simulate disk-full failures and prove
        # the atomic-write contract (the existing file is left untouched).
        os.replace(tmp_path, path)  # noqa: PTH105
        tmp_path = None  # replace consumed it
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
    models = data.setdefault("models", {})
    if not isinstance(models, dict):
        raise OpenclawConfigError("'models' must be a JSON object")
    providers = models.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise OpenclawConfigError("'models.providers' must be a JSON object")
    return providers


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

    Returns a diff dict ``{"before": <old entry or None>, "after": <new entry>}``
    describing the change.
    """
    data = read_config(path)
    providers = _ensure_providers(data)

    before = copy.deepcopy(providers.get(provider))

    entry: dict[str, Any] = dict(providers.get(provider) or {})
    entry["baseUrl"] = base_url
    if api_key is not None:
        entry["apiKey"] = api_key

    providers[provider] = entry

    _atomic_write_json(path, data)

    return {"before": before, "after": copy.deepcopy(entry)}


def unset_provider(path: Path, provider: str) -> dict[str, Any]:
    """Remove ``models.providers.<provider>`` entirely.

    Returns the removed entry as a dict, or ``{}`` if it was not present.
    Other providers are left untouched. The file is rewritten atomically.
    """
    data = read_config(path)
    if not data:
        return {}

    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    providers = models.get("providers")
    if not isinstance(providers, dict) or provider not in providers:
        return {}

    removed = copy.deepcopy(providers.pop(provider))
    _atomic_write_json(path, data)
    return removed if isinstance(removed, dict) else {}


def get_provider(path: Path, provider: str) -> dict[str, Any] | None:
    """Return ``models.providers.<provider>`` or ``None`` if absent."""
    data = read_config(path)
    if not data:
        return None

    models = data.get("models")
    if not isinstance(models, dict):
        return None
    providers = models.get("providers")
    if not isinstance(providers, dict):
        return None

    entry = providers.get(provider)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise OpenclawConfigError(f"provider '{provider}' is not a JSON object in {path}")
    return copy.deepcopy(entry)
