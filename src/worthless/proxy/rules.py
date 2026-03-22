"""Rules engine — gate-before-reconstruct pipeline (SR-03 / CRYP-05).

The rules engine evaluates a request BEFORE any key reconstruction occurs.
A denied request means zero KMS calls, zero reconstruction, zero key material.
"""

from __future__ import annotations

import time
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

    Queries spend_log and enrollment_config tables via a persistent aiosqlite
    connection. Uses BEGIN IMMEDIATE to serialize concurrent reads (H-3).
    Fails closed on any DB error (H-1/M-10).
    Returns None if no cap is configured (NULL spend_cap) or spend is under cap.
    Returns 402 ErrorResponse when cap is exceeded or on DB error.

    .. note:: PoC limitation — the spend cap is a best-effort pre-check, not a
       hard enforcement boundary. Token count is only known after the upstream
       response, so check (here) and record (in metering.py) are separate
       operations. Two concurrent requests can both pass the cap check before
       either records its spend. Production fix: reserve estimated tokens at
       check time, reconcile after response.
    """

    db: aiosqlite.Connection

    async def evaluate(self, alias: str, request: object) -> ErrorResponse | None:
        try:
            # BEGIN IMMEDIATE acquires a write lock, preventing TOCTOU races
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                # Check if there's a spend cap for this alias
                async with self.db.execute(
                    "SELECT spend_cap FROM enrollment_config WHERE key_alias = ?",
                    (alias,),
                ) as cur:
                    row = await cur.fetchone()

                if row is None:
                    await self.db.execute("ROLLBACK")
                    return None

                spend_cap = row[0]
                if spend_cap is None:
                    await self.db.execute("ROLLBACK")
                    return None

                # Sum tokens spent by this alias
                async with self.db.execute(
                    "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
                    (alias,),
                ) as cur:
                    (total_tokens,) = await cur.fetchone()  # type: ignore[assignment]

                await self.db.execute("ROLLBACK")  # read-only, release lock

                if total_tokens >= spend_cap:
                    return spend_cap_error_response()

                return None
            except Exception:
                # Ensure lock is released on inner error
                try:
                    await self.db.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        except Exception:
            # Fail-closed: any DB error -> deny request
            return spend_cap_error_response()


@dataclass
class RateLimitRule:
    """In-memory sliding window rate limiter keyed by (alias, client_ip).

    Uses time.monotonic() for window tracking. Not persisted across restarts.
    Returns 429 ErrorResponse with Retry-After when rate is exceeded.

    Periodically cleans up expired entries to bound memory usage (M-2).
    """

    default_rps: float = 100.0
    cleanup_interval: float = 60.0
    _windows: dict[tuple[str, str], list[float]] = field(
        default_factory=dict, init=False, repr=False
    )
    _last_cleanup: float = field(default=0.0, init=False, repr=False)

    async def evaluate(self, alias: str, request: object) -> ErrorResponse | None:
        client_ip = getattr(getattr(request, "client", None), "host", "unknown")
        now = time.monotonic()
        key = (alias, client_ip)

        # Periodic cleanup of stale entries (M-2: bound memory)
        if now - self._last_cleanup >= self.cleanup_interval:
            self._cleanup(now)
            self._last_cleanup = now

        # Prune timestamps older than 1 second for this key
        window = self._windows.get(key, [])
        cutoff = now - 1.0
        window = [t for t in window if t > cutoff]

        if len(window) >= self.default_rps:
            # Calculate retry-after: time until oldest entry expires
            retry_after = max(1, int(window[0] + 1.0 - now) + 1)
            self._windows[key] = window
            return rate_limit_error_response(retry_after=retry_after)

        window.append(now)
        self._windows[key] = window
        return None

    def _cleanup(self, now: float) -> None:
        """Remove all entries where the latest timestamp is older than 2 seconds."""
        ttl_cutoff = now - 2.0
        stale_keys = [
            k for k, timestamps in self._windows.items()
            if not timestamps or max(timestamps) < ttl_cutoff
        ]
        for k in stale_keys:
            del self._windows[k]
