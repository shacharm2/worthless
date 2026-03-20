"""Rules engine — gate-before-reconstruct pipeline (SR-03 / CRYP-05).

The rules engine evaluates a request BEFORE any key reconstruction occurs.
A denied request means zero KMS calls, zero reconstruction, zero key material.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import aiosqlite

from worthless.proxy.errors import (
    ErrorResponse,
    rate_limit_error_response,
    spend_cap_error_response,
)


@runtime_checkable
class Rule(Protocol):
    """Protocol for a single rule in the gate-before-reconstruct pipeline."""

    async def evaluate(self, alias: str, request: object) -> ErrorResponse | None: ...


@dataclass
class RulesEngine:
    """Ordered chain of rules. Short-circuits on first denial."""

    rules: list[Rule]

    async def evaluate(self, alias: str, request: object) -> ErrorResponse | None:
        for rule in self.rules:
            result = await rule.evaluate(alias, request)
            if result is not None:
                return result
        return None


@dataclass
class SpendCapRule:
    """Denies requests when accumulated spend exceeds the configured cap.

    Queries spend_log and enrollment_config tables via aiosqlite.
    Returns None if no cap is configured (NULL spend_cap) or spend is under cap.
    Returns 402 ErrorResponse when cap is exceeded.
    """

    db_path: str

    async def evaluate(self, alias: str, request: object) -> ErrorResponse | None:
        async with aiosqlite.connect(self.db_path) as db:
            # Check if there's a spend cap for this alias
            async with db.execute(
                "SELECT spend_cap FROM enrollment_config WHERE key_alias = ?",
                (alias,),
            ) as cur:
                row = await cur.fetchone()

            if row is None:
                # No enrollment config -> no cap -> pass
                return None

            spend_cap = row[0]
            if spend_cap is None:
                # Explicit NULL cap -> unlimited -> pass
                return None

            # Sum tokens spent by this alias
            async with db.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
                (alias,),
            ) as cur:
                (total_tokens,) = await cur.fetchone()  # type: ignore[assignment]

            if total_tokens >= spend_cap:
                return spend_cap_error_response()

        return None


@dataclass
class RateLimitRule:
    """In-memory sliding window rate limiter keyed by (alias, client_ip).

    Uses time.monotonic() for window tracking. Not persisted across restarts.
    Returns 429 ErrorResponse with Retry-After when rate is exceeded.
    """

    default_rps: float = 100.0
    _windows: dict[tuple[str, str], list[float]] = field(
        default_factory=lambda: defaultdict(list), init=False, repr=False
    )

    async def evaluate(self, alias: str, request: object) -> ErrorResponse | None:
        client_ip = getattr(getattr(request, "client", None), "host", "unknown")
        now = time.monotonic()
        key = (alias, client_ip)

        # Prune timestamps older than 1 second
        window = self._windows[key]
        cutoff = now - 1.0
        self._windows[key] = [t for t in window if t > cutoff]
        window = self._windows[key]

        if len(window) >= self.default_rps:
            # Calculate retry-after: time until oldest entry expires
            retry_after = max(1, int(window[0] + 1.0 - now) + 1)
            return rate_limit_error_response(retry_after=retry_after)

        window.append(now)
        return None
