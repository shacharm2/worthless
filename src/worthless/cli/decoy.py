"""Format-aware decoy key generation (WOR-31).

Generates CSPRNG-random keys that match exact provider formats (prefix,
charset, length, structural markers) so they are statistically
indistinguishable from real API keys.
"""

from __future__ import annotations

import secrets

from worthless.crypto.charsets import ALPHANUMERIC, BASE64URL

# Default random body length for unknown providers.
_FALLBACK_BODY_LEN = 40

PROVIDER_FORMATS: dict[str, dict] = {
    "openai": {
        "charset": BASE64URL,
        # Structure: prefix + 74 random + "T3BlbkFJ" marker + 74 random
        "segments": [
            ("random", 74),
            ("literal", "T3BlbkFJ"),
            ("random", 74),
        ],
    },
    "anthropic": {
        "charset": BASE64URL,
        # Structure: prefix + 93 random + "AA" suffix
        "segments": [
            ("random", 93),
            ("literal", "AA"),
        ],
    },
    "google": {
        "charset": BASE64URL,
        # Structure: prefix + 33 random
        "segments": [
            ("random", 33),
        ],
    },
    "xai": {
        "charset": ALPHANUMERIC,
        # Structure: prefix + 80 random
        "segments": [
            ("random", 80),
        ],
    },
}


def make_decoy(provider: str, prefix: str) -> str:
    """Generate a format-correct decoy key for *provider*.

    Uses ``secrets.choice()`` over the provider's exact charset for each
    random segment, producing output that is computationally
    indistinguishable from real keys.

    For unknown providers, generates ``prefix`` + 40 alphanumeric chars.
    """
    fmt = PROVIDER_FORMATS.get(provider)
    if fmt is None:
        body = "".join(secrets.choice(ALPHANUMERIC) for _ in range(_FALLBACK_BODY_LEN))
        return prefix + body

    charset = fmt["charset"]
    parts: list[str] = [prefix]
    for segment in fmt["segments"]:
        kind = segment[0]
        if kind == "random":
            length = segment[1]
            parts.append("".join(secrets.choice(charset) for _ in range(length)))
        elif kind == "literal":
            parts.append(segment[1])

    return "".join(parts)
