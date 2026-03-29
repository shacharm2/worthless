"""Path-based adapter lookup."""

from __future__ import annotations

from worthless.adapters.anthropic import AnthropicAdapter
from worthless.adapters.openai import OpenAIAdapter
from worthless.adapters.types import ProviderAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {
    "/v1/chat/completions": OpenAIAdapter(),
    "/v1/messages": AnthropicAdapter(),
}

# Path → provider name mapping (same vocabulary as CLI enrollment aliases)
_PATH_TO_PROVIDER: dict[str, str] = {
    "/v1/chat/completions": "openai",
    "/v1/messages": "anthropic",
}


def get_adapter(path: str) -> ProviderAdapter | None:
    """Return the adapter for the given request path, or None if unrecognized."""
    return _ADAPTERS.get(path)


def get_provider_for_path(path: str) -> str | None:
    """Return the provider name for a request path, or None if unrecognized."""
    return _PATH_TO_PROVIDER.get(path)
