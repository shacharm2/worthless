"""Provider prefix patterns and auto-detection for API keys."""

from __future__ import annotations

import re

# Ordered longest-first per provider for greedy matching.
PROVIDER_PREFIXES: dict[str, list[str]] = {
    "openai": ["sk-proj-", "sk-"],
    "anthropic": ["sk-ant-api03-", "sk-ant-", "anthropic-"],
    "google": ["AIza"],
    "xai": ["xai-"],
}

# Build a combined regex: any known prefix followed by 10+ word/dash chars.
_all_prefixes = sorted(
    (prefix for prefixes in PROVIDER_PREFIXES.values() for prefix in prefixes),
    key=len,
    reverse=True,
)
_prefix_pattern = "|".join(re.escape(p) for p in _all_prefixes)
KEY_PATTERN: re.Pattern[str] = re.compile(rf"(?:{_prefix_pattern})[\w\-]{{10,}}")


# Flat lookup sorted longest-first so "sk-ant-" beats "sk-".
_PREFIX_TO_PROVIDER: list[tuple[str, str]] = sorted(
    ((prefix, provider) for provider, prefixes in PROVIDER_PREFIXES.items() for prefix in prefixes),
    key=lambda t: len(t[0]),
    reverse=True,
)


def detect_provider(api_key: str) -> str | None:
    """Return the provider name for *api_key*, or ``None`` if unrecognised."""
    for prefix, provider in _PREFIX_TO_PROVIDER:
        if api_key.startswith(prefix):
            return provider
    return None


ENTROPY_THRESHOLD: float = 4.5


def detect_prefix(api_key: str, provider: str) -> str:
    """Return the matching prefix string for *api_key* given *provider*."""
    prefixes = PROVIDER_PREFIXES.get(provider, [])
    for prefix in prefixes:
        if api_key.startswith(prefix):
            return prefix
    raise ValueError(f"No matching prefix for provider {provider!r}")
