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
    token_budget_error_response,
)


@runtime_checkable
class Rule(Protocol):
    """Protocol for a single rule in the gate-before-reconstruct pipeline."""

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ) -> ErrorResponse | None: ...


@dataclass
class RulesEngine:
    """Ordered chain of rules. Short-circuits on first denial."""

    rules: list[Rule]

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ) -> ErrorResponse | None:
        for rule in self.rules:
            result = await rule.evaluate(alias, request, provider=provider, body=body)
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

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ) -> ErrorResponse | None:
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
                    return spend_cap_error_response(provider=provider)

                return None
            except Exception:
                # Ensure lock is released on inner error
                try:
                    await self.db.execute("ROLLBACK")
                except Exception:  # noqa: S110 — ROLLBACK on error path; if it fails, re-raise original anyway
                    pass
                raise
        except Exception:
            # Fail-closed: any DB error -> deny request
            return spend_cap_error_response(provider=provider)


@dataclass
class TokenBudgetRule:
    """Denies requests when token usage exceeds daily/weekly/monthly budgets.

    Queries spend_log with time-windowed SUM against enrollment_config budgets.
    Uses BEGIN IMMEDIATE like SpendCapRule for serialization.
    Fails closed on any DB error.
    Budget periods are UTC-anchored (SQLite datetime('now') is always UTC).

    .. note:: Same TOCTOU caveat as SpendCapRule — token count is only known
       after the upstream response. Two concurrent requests can both pass.
    """

    db: aiosqlite.Connection

    _PERIODS: tuple[tuple[str, str], ...] = (
        ("daily", "-1 day"),
        ("weekly", "-7 days"),
        ("monthly", "-30 days"),
    )

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ) -> ErrorResponse | None:
        try:
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                async with self.db.execute(
                    "SELECT token_budget_daily, token_budget_weekly,"
                    " token_budget_monthly"
                    " FROM enrollment_config WHERE key_alias = ?",
                    (alias,),
                ) as cur:
                    row = await cur.fetchone()

                if row is None:
                    await self.db.execute("ROLLBACK")
                    return None

                budgets = {
                    "daily": row[0],
                    "weekly": row[1],
                    "monthly": row[2],
                }

                # If all budgets are NULL, no limit
                if all(v is None for v in budgets.values()):
                    await self.db.execute("ROLLBACK")
                    return None

                # Single scan with conditional aggregation (efficiency: 1 query not 3)
                async with self.db.execute(
                    "SELECT"
                    " COALESCE(SUM(CASE WHEN created_at >= datetime('now','-1 day')"
                    "   THEN tokens END), 0),"
                    " COALESCE(SUM(CASE WHEN created_at >= datetime('now','-7 days')"
                    "   THEN tokens END), 0),"
                    " COALESCE(SUM(tokens), 0)"
                    " FROM spend_log"
                    " WHERE key_alias = ?"
                    " AND created_at >= datetime('now', '-30 days')",
                    (alias,),
                ) as cur:
                    used_daily, used_weekly, used_monthly = await cur.fetchone()  # type: ignore[assignment]

                usage = {
                    "daily": int(used_daily),
                    "weekly": int(used_weekly),
                    "monthly": int(used_monthly),
                }

                for period, _interval in self._PERIODS:
                    limit = budgets[period]
                    if limit is None:
                        continue
                    if usage[period] >= limit:
                        await self.db.execute("ROLLBACK")
                        return token_budget_error_response(
                            period=period,
                            used=usage[period],
                            limit=int(limit),
                            provider=provider,
                        )

                await self.db.execute("ROLLBACK")  # read-only, release lock
                return None
            except Exception:
                try:
                    await self.db.execute("ROLLBACK")
                except Exception:  # noqa: S110
                    pass
                raise
        except Exception:
            # Fail-closed: any DB error -> deny request
            return token_budget_error_response(period="unknown", used=0, limit=0, provider=provider)


@dataclass
class RateLimitRule:
    """In-memory sliding window rate limiter keyed by (alias, client_ip).

    Uses time.monotonic() for window tracking. Not persisted across restarts.
    Returns 429 ErrorResponse with Retry-After when rate is exceeded.

    Periodically cleans up expired entries to bound memory usage (M-2).

    Per-enrollment rate limits are read from enrollment_config.rate_limit_rps
    on first access and cached in memory. Falls back to default_rps.
    """

    default_rps: float = 100.0
    cleanup_interval: float = 60.0
    db_path: str | None = None
    _windows: dict[tuple[str, str], list[float]] = field(
        default_factory=dict, init=False, repr=False
    )
    _last_cleanup: float = field(default=0.0, init=False, repr=False)
    _limits: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    async def evaluate(
        self, alias: str, request: object, *, provider: str = "openai", body: bytes = b""
    ) -> ErrorResponse | None:
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

        # Use per-enrollment limit if available, otherwise default
        if alias not in self._limits and self.db_path is not None:
            await self._load_limit(alias)
        rps = self._limits.get(alias, self.default_rps)

        if len(window) >= rps:
            # Calculate retry-after: time until oldest entry expires
            retry_after = max(1, int(window[0] + 1.0 - now) + 1)
            self._windows[key] = window
            return rate_limit_error_response(retry_after=retry_after, provider=provider)

        window.append(now)
        self._windows[key] = window
        return None

    def _cleanup(self, now: float) -> None:
        """Remove all entries where the latest timestamp is older than 2 seconds."""
        ttl_cutoff = now - 2.0
        stale_keys = [
            k
            for k, timestamps in self._windows.items()
            if not timestamps or max(timestamps) < ttl_cutoff
        ]
        for k in stale_keys:
            del self._windows[k]

    async def _load_limit(self, alias: str) -> None:
        """Load per-enrollment rate limit from DB into cache."""
        async with aiosqlite.connect(self.db_path) as db:  # type: ignore[arg-type]
            async with db.execute(
                "SELECT rate_limit_rps FROM enrollment_config WHERE key_alias = ?",
                (alias,),
            ) as cur:
                row = await cur.fetchone()
        if row is not None and row[0] is not None:
            self._limits[alias] = float(row[0])
