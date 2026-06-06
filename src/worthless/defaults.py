"""Default configuration values applied at enrollment time."""

from __future__ import annotations

#: Default spend cap in tokens applied to new enrollments — roughly $10k at
#: typical 2026 prices (~$2.5k on cheap input, ~$75k on premium output; tokens
#: are an inexact proxy for dollars across models). A runaway-protection
#: ceiling, not a tight budget. Override per-key with ``--spend-cap``, or
#: ``None`` for unlimited.
DEFAULT_SPEND_CAP_TOKENS: int = 1_000_000_000
