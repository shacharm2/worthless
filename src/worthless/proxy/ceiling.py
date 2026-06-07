"""Global ceiling for the spend cap fallback path (WOR-696).

> Make sure the spend cap actually holds on every request — so once the budget's
> blown, the key stops forming, no matter who's spending or why.

This module answers ONE question:

  "When something goes wrong (mid-stream disconnect, unreadable usage, stream
   kill) and the original reservation was 0, what should we charge instead
   of 0?"

That number is ``GLOBAL_CEILING_TOKENS``.

**Design choice: one global ceiling, NO model registry.**

The cap is denominated in tokens, the ceiling only fires on the rare edge
case where the request didn't tell us its size AND something went wrong
mid-flight. We don't need to know what model the request used — any
fallback charges the same conservative number.

This is also the answer for OpenRouter / Azure / Enterprise / custom-URL
deployments: we accept ANY model string the user sends. Worthless is
maximally passthrough on the request itself; the cap fires post-hoc on
the fallback path without ever looking at the model name.

**Direction of error is conservative — we never under-bill.** A
gpt-4o-mini disconnect bills ~$0.019 instead of ~$0.002 (2 cents
over-bill). A gpt-5 disconnect bills accurately. Conservatism is the
correct direction for a security control.

**Why 128K specifically?** It's the highest documented max-output across
every major model in 2026-06 (OpenAI gpt-5 family, Anthropic Opus 4.5).
If a future model documents a higher max-output, bump this with a
comment naming the source. Bumping is always safe; lowering would
silently under-bill.

**Reversibility:** if we ever ship a real-time dollar-tracking feature
that needs per-model pricing, this becomes a function call
``ceiling_for(provider, model)`` returning per-model numbers. No
migration needed; the settle path just reads a different value.
"""

from __future__ import annotations

#: Token ceiling used on the settle_at_estimate fallback path when the
#: original reservation was 0. Sized to the worst-case documented
#: max-output across every supported model as of 2026-06-07 — OpenAI
#: gpt-5 family and Anthropic claude-opus-4-5 both list 128K.
GLOBAL_CEILING_TOKENS: int = 128_000
