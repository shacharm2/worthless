"""Provider URL → protocol registry loader (worthless-8rqs).

The registry maps known LLM-provider upstream URLs (e.g.,
``https://api.openai.com/v1``) to a wire protocol (``openai`` /
``anthropic``). ``worthless lock`` consults it when scanning ``.env``
files, so the user doesn't have to specify ``--protocol`` for the common
case.

Two sources, merged at load time:

1. **Bundled** — ``src/worthless/providers.toml`` ships with the package.
   Source of truth for upstream-blessed providers.
2. **User override** — ``~/.worthless/providers.toml`` (optional). Allows
   users to register internal LLM gateways or staging endpoints without
   modifying the package.

On URL conflict, the user entry wins. A malformed user file is skipped
with a logged warning (the bundled registry alone is enough to keep
``worthless lock`` working).
"""

from __future__ import annotations

import logging
import sys
from importlib import resources
from pathlib import Path
from typing import NamedTuple

# Python 3.10 ships without ``tomllib``; ``tomli`` is the canonical backport
# and is already a conditional dependency in ``pyproject.toml``.
if sys.version_info >= (3, 11):  # pragma: no cover — version branch
    import tomllib
else:  # pragma: no cover — version branch
    import tomli as tomllib

logger = logging.getLogger(__name__)


class ProviderEntry(NamedTuple):
    """One row in the provider registry."""

    name: str
    url: str
    protocol: str  # "openai" | "anthropic"


def _user_registry_path() -> Path:
    """Return the user-override path. Reads ``HOME`` so tests can redirect."""
    return Path.home() / ".worthless" / "providers.toml"


def _parse_toml_to_entries(raw: dict) -> dict[str, ProviderEntry]:
    """Convert a parsed-TOML dict into a URL-keyed entry map.

    Expected structure:
        [provider.<name>]
        url = "..."
        protocol = "..."
    """
    out: dict[str, ProviderEntry] = {}
    providers = raw.get("provider", {})
    for name, body in providers.items():
        if not isinstance(body, dict):
            continue
        url = body.get("url")
        protocol = body.get("protocol")
        if not isinstance(url, str) or not isinstance(protocol, str):
            logger.warning("provider %r has missing url or protocol; skipping", name)
            continue
        out[url] = ProviderEntry(name=name, url=url, protocol=protocol)
    return out


def load_bundled() -> dict[str, ProviderEntry]:
    """Load ``src/worthless/providers.toml`` shipped with the package."""
    bundle_text = resources.files("worthless").joinpath("providers.toml").read_text()
    parsed = tomllib.loads(bundle_text)
    return _parse_toml_to_entries(parsed)


def load_user() -> dict[str, ProviderEntry]:
    """Load the optional user override, returning {} on any failure mode."""
    path = _user_registry_path()
    if not path.is_file():
        return {}
    try:
        parsed = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning(
            "could not parse user providers.toml at %s — falling back to bundled registry only: %s",
            path,
            exc,
        )
        return {}
    return _parse_toml_to_entries(parsed)


def load_registry() -> dict[str, ProviderEntry]:
    """Return the merged registry: bundled with user-override layered on top."""
    return {**load_bundled(), **load_user()}


def lookup_by_url(url: str) -> ProviderEntry | None:
    """Return the entry for a given URL, or ``None`` if unknown."""
    return load_registry().get(url)


def bundled_names() -> set[str]:
    """Return the set of names registered in the bundled file (used to refuse
    name collisions when registering a user provider)."""
    return {e.name for e in load_bundled().values()}


def user_registry_path() -> Path:
    """Public accessor for ``~/.worthless/providers.toml``."""
    return _user_registry_path()


def lookup_by_name(name: str) -> ProviderEntry | None:
    """Return the entry for a given provider name, or ``None`` if unknown.
    Used by ``worthless lock`` to fall back to the canonical URL when the
    user's ``.env`` has an API key but no ``*_BASE_URL`` companion."""
    for entry in load_registry().values():
        if entry.name == name:
            return entry
    return None
