"""Per-(provider, model) ceiling lookup for the spend cap fallback path (WOR-696).

> Make sure the spend cap actually holds on every request — so once the budget's
> blown, the key stops forming, no matter who's spending or why.

This module answers two questions:

  1. "Is this (provider, model) one Worthless knows about?" — admission path
     uses this to fail-closed-reject unknown models BEFORE reconstruction.

  2. "When something goes wrong (mid-stream disconnect, unreadable usage,
     stream kill) and the original reservation was 0, what should we charge
     instead of 0?" — `settle_at_estimate` uses this number.

**Design choice: one global ceiling, not a per-model map.**

The ceiling only fires on the rare edge case where the request didn't tell us
its size AND something went wrong mid-flight. Optimizing it per-model is
busywork that buys ~2 cents of accuracy per disconnect event for gpt-4o-mini
users. We instead pick the **highest documented max-output across all
currently-supported models** (128K tokens, set by OpenAI gpt-5 and Anthropic
Opus 4.5 as of 2026-06) as the global ceiling.

**Direction of error is conservative — we never under-bill.** Sometimes we
over-bill on the rare disconnect path; that direction is the correct one for
a security control. The honest user paying max_tokens=512 against gpt-4o-mini
never hits the fallback path at all.

**Reversibility:** if we ever need per-model dollar precision (e.g., for a
real-time spend-dashboard feature), `KNOWN_MODELS` set + `ceiling_for` int
can be upgraded to a dict in a two-line change with zero migration.
"""

from __future__ import annotations

# The highest documented max-output across every model in KNOWN_MODELS as of
# 2026-06-07. Sources: OpenAI API reference (gpt-5 family = 128K); Anthropic
# docs (claude-opus-4-5 = 128K with output-128k beta header headroom).
#
# If a future model lists a higher max-output, bump this number FIRST (with a
# comment naming the model + cite URL), then add the entry to KNOWN_MODELS.
# Bumping is always safe; lowering would silently under-bill.
GLOBAL_CEILING_TOKENS: int = 128_000

# (provider, model) tuples for every model Worthless currently supports for
# the cap-gated path. Add new models here when they ship; the cost is one
# line per addition. Unknown models are rejected with WRTLS-150 at admission.
#
# The set is intentionally small. Major-version aliases (e.g. "gpt-4o" and
# "gpt-4o-2024-08-06") are listed separately because they're separate strings
# in the request body — the wire format is the source of truth.
KNOWN_MODELS: frozenset[tuple[str, str]] = frozenset(
    {
        # OpenAI — GPT-4 / 4o families (still production as of 2026-06)
        ("openai", "gpt-4"),
        ("openai", "gpt-4-turbo"),
        ("openai", "gpt-4o"),
        ("openai", "gpt-4o-mini"),
        ("openai", "gpt-4o-2024-08-06"),
        ("openai", "gpt-4o-2024-11-20"),
        ("openai", "gpt-4o-mini-2024-07-18"),
        # OpenAI — reasoning models (use max_completion_tokens, not max_tokens)
        ("openai", "o1"),
        ("openai", "o1-mini"),
        ("openai", "o3"),
        ("openai", "o3-mini"),
        ("openai", "o4-mini"),
        # OpenAI — GPT-5 family
        ("openai", "gpt-5"),
        ("openai", "gpt-5-mini"),
        ("openai", "gpt-5-nano"),
        # Anthropic — Claude 3 family (still production via Bedrock & direct)
        ("anthropic", "claude-3-haiku-20240307"),
        ("anthropic", "claude-3-sonnet-20240229"),
        ("anthropic", "claude-3-opus-20240229"),
        # Anthropic — Claude 3.5 family (aliases + snapshots)
        ("anthropic", "claude-3-5-sonnet"),
        ("anthropic", "claude-3-5-sonnet-latest"),
        ("anthropic", "claude-3-5-sonnet-20241022"),
        ("anthropic", "claude-3-5-sonnet-20240620"),
        ("anthropic", "claude-3-5-haiku-20241022"),
        ("anthropic", "claude-3-5-haiku-latest"),
        # Anthropic — Claude 3.7 / 4.x families
        ("anthropic", "claude-3-7-sonnet"),
        ("anthropic", "claude-opus-4"),
        ("anthropic", "claude-opus-4-5"),
        ("anthropic", "claude-sonnet-4"),
        ("anthropic", "claude-sonnet-4-5"),
        ("anthropic", "claude-haiku-4"),
        ("anthropic", "claude-haiku-4-5"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    }
)


def is_known_model(provider: str, model: str) -> bool:
    """True if Worthless recognizes this (provider, model) pair.

    Admission path uses this to reject unknown models with WRTLS-150 BEFORE
    the rules engine reserves anything and BEFORE the proxy attempts to
    reconstruct the API key. Unknown = fail-closed.
    """
    return (provider, model) in KNOWN_MODELS


def ceiling_for(provider: str, model: str) -> int | None:
    """The token ceiling to use for the settle_at_estimate fallback.

    Returns ``GLOBAL_CEILING_TOKENS`` if the (provider, model) pair is known,
    or ``None`` if it isn't. Callers MUST handle the ``None`` case explicitly
    — `settle_at_estimate` should never reach this lookup for an unknown
    model because admission already rejected it; the ``None`` return is a
    safety guard, not a normal path.
    """
    if not is_known_model(provider, model):
        return None
    return GLOBAL_CEILING_TOKENS
