"""Token extraction from provider responses and async spend recording.

Dual-phase metering:
    * Hot path (Redis) — fast ``GET`` / atomic ``INCRBY`` for spend-cap
      enforcement before key reconstruction. Consulted by
      :class:`worthless.proxy.rules.SpendCapRule`.
    * Durable ledger (SQLite) — ``spend_log`` insert after the upstream
      response has landed, used for reporting and budget windows.

The ledger is authoritative. Redis is a cache whose counter is rebuildable
from ``SELECT SUM(tokens) FROM spend_log``. The rule rehydrates the counter
on every cache miss (cold start, LRU eviction, restart, tamper) and falls
back to the SQLite path on any Redis transport error. Redis is optional;
when ``WORTHLESS_REDIS_URL`` is unset, the gate uses SQLite end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import aiosqlite

if TYPE_CHECKING:
    from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)

SPEND_KEY_PREFIX = "worthless:spend:"

# Must match the _ALIAS_RE in app.py. Duplicated here as defense in depth —
# spend_key() is a shared helper; every call site must get the same shape
# regardless of whether the alias came from the URL gate, a reseed task, or
# an admin API we haven't written yet.
_ALIAS_RE = re.compile(r"[a-zA-Z0-9_-]+")

# Hot-path counter values are at most ~20 bytes (uint64 max digit count).
# Anything larger is tamper or a bug in the Redis client — reject before
# int() parsing so we never buffer pathological payloads (worthless-35j1).
_MAX_COUNTER_RAW_BYTES = 32

# Only redis:// and rediss:// are accepted. unix:// and other schemes that
# redis-py's from_url would otherwise honour could be used by a compromised
# env var to redirect the counter to an attacker-controlled socket.
_ALLOWED_REDIS_SCHEMES = frozenset({"redis", "rediss"})

# Bounded timeouts so a hung Redis cannot park a request handler forever.
# Values deliberately tight: the gate is on the hot path of every request.
_REDIS_SOCKET_TIMEOUT = 2.0
_REDIS_CONNECT_TIMEOUT = 1.0


class RedisValueError(RuntimeError):
    """Raised when the hot-path counter is non-integer (tamper or corruption).

    Callers treat this as a cache miss — rehydrate from SQLite rather than
    silently returning 0, which would bypass the cap.
    """


class SpendDirtyTracker:
    """Tracks aliases whose Redis counter may be stale (worthless-woh7).

    When ``record_spend`` successfully writes to SQLite but the follow-up
    Redis ``INCRBY`` fails (transient network blip, Redis flap, etc.), the
    counter silently lags the authoritative ledger. ``SpendCapRule`` would
    otherwise trust the stale counter on the next request.

    This tracker is the signal between the writer (record_spend) and the
    reader (SpendCapRule._evaluate_redis). Writer marks the alias dirty
    on INCR failure; reader checks the flag, forces a rehydrate from
    SQLite (``rehydrate_spend_hot(..., force=True)``), and clears the
    flag.

    Process-scoped by design. On proxy restart the flag is lost — but so
    is the Redis counter (compose config has no persistence), so the
    first post-restart read hits the cache-miss path and rehydrates
    anyway.
    """

    # Hard ceiling for the dirty set (worthless-5w32). Oldest entries drop
    # when the cap is reached — FIFO, so the freshest drift signals (which
    # are the most useful) survive eviction. Defaults sized for ~10k
    # unique aliases per proxy process, well above ordinary traffic but
    # bounded enough to fail-loud on pathological load (~100 KB memory at
    # 10 KB ceiling with typical alias lengths).
    _DEFAULT_MAX_ENTRIES = 10_000

    def __init__(self, max_entries: int | None = None) -> None:
        self._max_entries: int = (
            max_entries if max_entries is not None else self._DEFAULT_MAX_ENTRIES
        )
        # OrderedDict gives us insertion-order iteration for FIFO eviction.
        # We only use keys; values are all None.
        self._dirty: OrderedDict[str, None] = OrderedDict()
        self._lock = asyncio.Lock()

    async def mark(self, alias: str) -> None:
        async with self._lock:
            # move_to_end so a re-mark of an existing alias refreshes its
            # position and doesn't get evicted ahead of older stale entries.
            if alias in self._dirty:
                self._dirty.move_to_end(alias)
            else:
                self._dirty[alias] = None
                # Evict oldest until we're back under the ceiling.
                while len(self._dirty) > self._max_entries:
                    self._dirty.popitem(last=False)

    async def is_dirty(self, alias: str) -> bool:
        async with self._lock:
            return alias in self._dirty

    async def clear(self, alias: str) -> None:
        async with self._lock:
            self._dirty.pop(alias, None)


def spend_key(alias: str) -> str:
    """Return the Redis key that holds the hot-path spend counter for ``alias``.

    Validates alias internally (worthless-onqa) — the URL gate's regex
    covers the HTTP path, but every other caller must also pass through
    this helper, so the check goes here as defense in depth.
    """
    if not isinstance(alias, str):
        raise TypeError(f"alias must be str, got {type(alias).__name__}")
    if not _ALIAS_RE.fullmatch(alias):
        raise ValueError(
            f"alias {alias!r} does not match {_ALIAS_RE.pattern} — "
            "reject-at-spend_key guards against Redis-key injection via "
            "a caller that bypasses the URL gate."
        )
    return f"{SPEND_KEY_PREFIX}{alias}"


async def create_redis_client(url: str) -> AsyncRedis:
    """Create an async Redis client from a connection URL.

    Validates the scheme, applies bounded socket timeouts, and pings the
    server so a typo'd URL fails at boot instead of on the first request.
    Lazily imports ``redis.asyncio`` so the dependency stays optional.
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in _ALLOWED_REDIS_SCHEMES:
        raise ValueError(
            f"WORTHLESS_REDIS_URL scheme {scheme!r} not allowed; "
            f"use one of {sorted(_ALLOWED_REDIS_SCHEMES)}"
        )
    try:
        from redis.asyncio import Redis
    except ImportError as exc:
        raise ImportError(
            "WORTHLESS_REDIS_URL is set but the 'redis' package is not installed. "
            "Install with: pip install 'worthless[redis]'"
        ) from exc
    client = Redis.from_url(
        url,
        decode_responses=False,
        socket_timeout=_REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=_REDIS_CONNECT_TIMEOUT,
        health_check_interval=30,
    )
    # redis-py's async type stubs mark ping() as returning bool, not
    # Awaitable[bool] — it *is* a coroutine at runtime. Cast away.
    await client.ping()  # type: ignore[misc]
    return client


async def get_spend_hot(redis: AsyncRedis | Any, alias: str) -> int | None:
    """Return the hot-path token counter for ``alias``, or ``None`` on cache miss.

    * ``None`` — key absent (cold start, evicted, restart, flushed).
    * ``int`` — the stored counter.
    * Raises :class:`RedisValueError` — stored value is not an integer,
      or exceeds the sane byte size for a counter (tampering or
      corruption — worthless-35j1). Callers must NOT treat this as 0.
    * Raises on any transport error — callers decide policy.
    """
    raw = await redis.get(spend_key(alias))
    if raw is None:
        return None
    if isinstance(raw, bytes | bytearray) and len(raw) > _MAX_COUNTER_RAW_BYTES:
        raise RedisValueError(
            f"hot-path counter for alias={alias!r} is oversized "
            f"({len(raw)} bytes > {_MAX_COUNTER_RAW_BYTES}); treating as tamper"
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RedisValueError(f"hot-path counter for alias={alias!r} is non-integer") from exc


async def incr_spend_hot(redis: AsyncRedis | Any, alias: str, tokens: int) -> int:
    """Atomically add ``tokens`` to the hot-path counter; return the new total."""
    if tokens <= 0:
        current = await get_spend_hot(redis, alias)
        return 0 if current is None else current
    return int(await redis.incrby(spend_key(alias), tokens))


async def sum_spend_sqlite(db: aiosqlite.Connection, alias: str) -> int:
    """Return total tokens spent by ``alias`` from the authoritative SQLite ledger."""
    async with db.execute(
        "SELECT COALESCE(SUM(tokens), 0) FROM spend_log WHERE key_alias = ?",
        (alias,),
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def rehydrate_spend_hot(
    redis: AsyncRedis | Any,
    db: aiosqlite.Connection,
    alias: str,
    *,
    force: bool = False,
) -> int:
    """Warm the hot-path counter from SQLite. Returns the authoritative total.

    When ``force`` is ``False`` (default, used on cache miss), a ``SET NX``
    is issued so a concurrent warmer / fresher writer wins. When ``force``
    is ``True`` (used by drift recovery — worthless-woh7), a plain ``SET``
    overwrites any stale value.

    Best-effort on the SET: if it fails we still return the SQLite total
    so the caller can deny off the authoritative number.
    """
    total = await sum_spend_sqlite(db, alias)
    try:
        if force:
            await redis.set(spend_key(alias), total)
        else:
            await redis.set(spend_key(alias), total, nx=True)
    except Exception:
        logger.warning(
            "Failed to warm hot-path counter for alias=%s; SQLite total used for this request",
            alias,
        )
    return total


@dataclass(frozen=True)
class UsageInfo:
    """Extracted token usage from a provider response."""

    total_tokens: int
    model: str | None


def extract_usage_openai(data: bytes) -> UsageInfo | None:
    """Extract token usage from an OpenAI response (JSON or SSE).

    For JSON responses: parses usage.total_tokens and model directly.
    For SSE streams: scans for the final chunk containing a "usage" field.
    Returns None if usage data is not found or data is malformed.
    """
    if not data:
        return None

    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "usage" in parsed:
            total = parsed["usage"].get("total_tokens", 0)
            return UsageInfo(total_tokens=total, model=parsed.get("model"))
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        text = data.decode("utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
                if isinstance(chunk, dict) and "usage" in chunk:
                    total = chunk["usage"].get("total_tokens", 0)
                    return UsageInfo(total_tokens=total, model=chunk.get("model"))
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception:  # noqa: S110 — best-effort SSE decode; malformed response must not raise  # nosec B110
        pass

    return None


def _find_sse_event_data(
    lines: list[str],
    event_name: str,
    *,
    reverse: bool = False,
) -> dict | None:
    """Find an SSE event by name and parse its data payload."""
    indices = range(len(lines) - 1, -1, -1) if reverse else range(len(lines))
    for i in indices:
        if lines[i].strip() == f"event: {event_name}":
            for j in range(i + 1, len(lines)):
                data_line = lines[j].strip()
                if data_line.startswith("data: "):
                    try:
                        return json.loads(data_line[6:])
                    except (json.JSONDecodeError, ValueError):
                        return None
    return None


def extract_usage_anthropic(data: bytes) -> UsageInfo | None:
    """Extract token usage from an Anthropic response (JSON or SSE).

    For non-streaming JSON: parses usage.input_tokens + usage.output_tokens directly.
    For SSE streams: scans for message_start (input_tokens) and message_delta (output_tokens).
    Returns None if no usage data found.
    """
    if not data:
        return None

    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and "usage" in parsed:
            usage = parsed["usage"]
            input_tokens = usage.get("input_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            return UsageInfo(
                total_tokens=input_tokens + cache_creation + cache_read + output_tokens,
                model=parsed.get("model"),
            )
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
    except Exception:  # noqa: S110 — best-effort SSE decode; malformed response must not raise
        return None

    input_tokens = 0
    model: str | None = None

    start = _find_sse_event_data(lines, "message_start")
    if start:
        msg = start.get("message", {})
        usage = msg.get("usage", {})
        input_tokens = (
            usage.get("input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
        )
        model = msg.get("model")

    delta = _find_sse_event_data(lines, "message_delta", reverse=True)
    if delta is None or "usage" not in delta:
        return None

    output_tokens = delta["usage"].get("output_tokens", 0)
    return UsageInfo(total_tokens=input_tokens + output_tokens, model=model)


class StreamingUsageCollector:
    """Incrementally extract usage from SSE chunks without buffering.

    Processes each chunk as it arrives, extracting only usage-bearing
    data. Does not store raw chunks — bounded memory regardless of
    stream length.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self._partial_line: str = ""
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._total_tokens: int | None = None
        self._model: str | None = None
        self._pending_event: str | None = None
        self._found_usage = False

    # No legitimate SSE line exceeds 64KB; cap _partial_line to prevent
    # a malicious upstream without newlines from growing it unbounded.
    _MAX_PARTIAL_LINE = 65_536

    def feed(self, chunk: bytes) -> None:
        """Process an SSE chunk, extracting usage data."""
        text = self._partial_line + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # Last element may be incomplete — save for next feed
        partial = lines[-1]
        if len(partial) > self._MAX_PARTIAL_LINE:
            partial = ""  # discard oversized partial — no legitimate SSE line is this big
        self._partial_line = partial

        for line in lines[:-1]:
            stripped = line.strip()
            if stripped.startswith("event: "):
                self._pending_event = stripped[7:]
            elif stripped.startswith("data: "):
                payload = stripped[6:]
                if payload == "[DONE]":
                    continue
                self._parse_data(payload)

    def _parse_data(self, payload: str) -> None:
        """Parse a single SSE data line and extract usage if present."""
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return

        if not isinstance(parsed, dict):
            return

        if self.provider == "openai":
            usage = parsed.get("usage")
            if usage and isinstance(usage, dict):
                self._total_tokens = usage.get("total_tokens", 0)
                self._model = parsed.get("model", self._model)
                self._found_usage = True
        elif self.provider == "anthropic":
            if self._pending_event == "message_start":
                msg = parsed.get("message")
                if not isinstance(msg, dict):
                    return
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    self._input_tokens = (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                    )
                self._model = msg.get("model", self._model)
            elif self._pending_event == "message_delta":
                usage = parsed.get("usage")
                if not isinstance(usage, dict):
                    return
                if "output_tokens" in usage:
                    self._output_tokens = usage["output_tokens"]
                    self._found_usage = True

        self._pending_event = None

    def _flush_partial(self) -> None:
        """Parse any leftover data in _partial_line before returning results."""
        if self._partial_line:
            stripped = self._partial_line.strip()
            if stripped.startswith("data: "):
                payload = stripped[6:]
                if payload != "[DONE]":
                    self._parse_data(payload)
            elif stripped.startswith("event: "):
                self._pending_event = stripped[7:]
            self._partial_line = ""

    def result(self) -> UsageInfo | None:
        """Return extracted usage after stream ends."""
        self._flush_partial()
        if self.provider == "openai":
            if self._total_tokens is not None:
                return UsageInfo(total_tokens=self._total_tokens, model=self._model)
            return None
        elif self.provider == "anthropic":
            if not self._found_usage:
                return None
            return UsageInfo(
                total_tokens=self._input_tokens + self._output_tokens,
                model=self._model,
            )
        return None


async def record_spend(
    db_path: str,
    alias: str,
    tokens: int,
    model: str | None,
    provider: str,
    redis: AsyncRedis | Any | None = None,
    dirty_tracker: SpendDirtyTracker | None = None,
) -> None:
    """Durably record spend to SQLite and (best-effort) increment the Redis counter.

    The SQLite insert is the source of truth. The Redis hot-path counter is an
    eventually-consistent cache used by :class:`SpendCapRule`; a Redis failure
    here is logged but does NOT fail the request — the request has already been
    served. The gate itself remains fail-closed on reads (see ``SpendCapRule``).

    When a ``dirty_tracker`` is supplied and the INCR fails, the alias is
    marked dirty so the next ``SpendCapRule._evaluate_redis`` forces a
    rehydrate from SQLite (worthless-woh7 — bounds counter drift).
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO spend_log (key_alias, tokens, model, provider) VALUES (?, ?, ?, ?)",
            (alias, tokens, model, provider),
        )
        await db.commit()

    if redis is not None and tokens > 0:
        try:
            await incr_spend_hot(redis, alias, tokens)
        except Exception:
            if dirty_tracker is not None:
                try:
                    await dirty_tracker.mark(alias)
                except Exception:
                    # Tracker must never propagate — SQLite is still authoritative.
                    logger.warning(
                        "SpendDirtyTracker.mark raised for alias=%s; "
                        "drift detection may miss this write",
                        alias,
                    )
            logger.warning(
                "Failed to increment Redis hot-path counter for alias=%s; "
                "SQLite ledger is authoritative",
                alias,
            )
