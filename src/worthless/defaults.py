"""Default configuration values applied at enrollment time."""

from __future__ import annotations

#: Default spend cap in tokens applied to new enrollments.
#: Override per-key with ``--spend-cap`` or ``None`` for unlimited.
DEFAULT_SPEND_CAP_TOKENS: int = 10_000_000
