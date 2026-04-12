"""FastAPI proxy app — gate-before-reconstruct pipeline.

This is the core Worthless product: every request passes through the rules engine
BEFORE any key reconstruction occurs. Denied requests never touch key material.

Architecture invariants enforced:
1. Gate before reconstruct (CRYP-05 / SR-03)
2. Transparent routing via adapter registry (PROX-04)
3. Server-side-only reconstruction — key never in response (PROX-05)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from starlette.middleware.cors import CORSMiddleware

from worthless.adapters.registry import get_adapter, get_provider_for_path
from worthless.adapters.types import INTERNAL_HEADER_PREFIX
from worthless.crypto.splitter import reconstruct_key, secure_key
from worthless.proxy.config import ProxySettings
from worthless.proxy.errors import _error_body, auth_error_response, gateway_error_response
from worthless.proxy.metering import (
    StreamingUsageCollector,
    extract_usage_anthropic,
    extract_usage_openai,
    record_spend,
)
from worthless.proxy.rules import (
    RateLimitRule,
    RulesEngine,
    SpendCapRule,
    TokenBudgetRule,
)
from worthless.storage.repository import ShardRepository
from worthless.storage.schema import SCHEMA

logger = logging.getLogger(__name__)

_ALIAS_RE = re.compile(r"[a-zA-Z0-9_-]+")
_BAD_HEADER_CHARS = frozenset("\x00\r\n")


def _make_uniform_401_bytes() -> tuple[bytes, dict[str, str]]:
    """Pre-compute the uniform 401 body so all code paths return the exact same bytes."""
    err = auth_error_response()
    return err.body, err.headers


# Pre-computed uniform response
_AUTH_BODY, _AUTH_HEADERS = _make_uniform_401_bytes()


def _uniform_401() -> Response:
    """Return the uniform 401 response (anti-enumeration)."""
    return Response(
        content=_AUTH_BODY,
        status_code=401,
        headers=_AUTH_HEADERS,
        media_type="application/json",
    )


def _infer_alias_from_path(clean_path: str, settings: ProxySettings) -> str | None:
    """Infer alias from request path when x-worthless-key header is absent.

    Maps the path to a provider via the adapter registry, then scans
    shard_a_dir for a unique matching alias (format: ``provider-hash8``).
    Returns None if no match or ambiguous (multiple aliases for same provider).
    """
    provider = get_provider_for_path(clean_path)
    if not provider:
        return None

    shard_a_dir = Path(settings.shard_a_dir)
    if not shard_a_dir.exists():
        return None

    matches = [
        f.name for f in shard_a_dir.iterdir() if f.is_file() and f.name.startswith(f"{provider}-")
    ]
    if len(matches) == 1:
        return matches[0]

    # Zero or multiple matches — cannot infer unambiguously
    if len(matches) > 1:
        logger.warning(
            "Ambiguous alias inference: %d aliases for provider %r. "
            "Use x-worthless-key header or enroll only one key per provider.",
            len(matches),
            provider,
        )
    return None


def _strip_worthless_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove x-worthless-* headers from a response header dict."""
    return {k: v for k, v in headers.items() if not k.lower().startswith(INTERNAL_HEADER_PREFIX)}


def _sanitize_upstream_error(status_code: int, body: bytes, provider: str) -> bytes:
    """Sanitize upstream error response body — strip internal provider details.

    Keeps the error type but replaces the message with a generic one
    to prevent information leakage from the upstream provider.
    """
    error_type = "api_error"
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and "error" in parsed and isinstance(parsed["error"], dict):
            error_type = parsed["error"].get("type", "api_error")
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    return _error_body(status_code, "upstream provider error", error_type, provider)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the proxy."""
    settings: ProxySettings = app.state.settings

    db = await aiosqlite.connect(settings.db_path)
    await db.executescript(SCHEMA)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.commit()
    app.state.db = db

    repo = ShardRepository(settings.db_path, settings.fernet_key)
    await repo.initialize()
    app.state.repo = repo

    client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(
            connect=10.0,
            read=settings.streaming_timeout,
            write=settings.upstream_timeout,
            pool=10.0,
        ),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    app.state.httpx_client = client

    rules_engine = RulesEngine(
        rules=[
            SpendCapRule(db=db),
            TokenBudgetRule(db=db),
            RateLimitRule(
                default_rps=settings.default_rate_limit_rps,
                db_path=settings.db_path,
            ),
        ]
    )
    app.state.rules_engine = rules_engine

    yield

    # Cleanup
    await client.aclose()
    await db.close()
    repo.close()
    # Zero the settings key material (SR-02)
    for i in range(len(settings.fernet_key)):
        settings.fernet_key[i] = 0


def create_app(settings: ProxySettings | None = None) -> FastAPI:
    """Create the Worthless proxy FastAPI app.

    Args:
        settings: Proxy settings. If None, loads from environment.
    """
    if settings is None:
        settings = ProxySettings()

    settings.validate()

    app = FastAPI(
        title="worthless-proxy",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = settings

    # Middleware stack (reverse order: last registered runs first)
    app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET"], allow_headers=[])

    # Health endpoints (no auth)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, object]:
        count = 0
        try:
            db: aiosqlite.Connection = request.app.state.db
            async with db.execute("SELECT COUNT(*) FROM spend_log") as cursor:
                row = await cursor.fetchone()
                if row:
                    count = row[0]
        except Exception:  # noqa: S110 — spend_log may not exist yet
            pass
        return {"status": "ok", "requests_proxied": count}

    @app.get("/readyz")
    async def readyz(request: Request) -> Response:
        # H-3: Only check DB connectivity — never reveal enrollment state
        # (prevents unauthenticated enrollment oracle, worthless-9dz)
        db: aiosqlite.Connection = request.app.state.db
        try:
            await db.execute("SELECT 1")
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(status_code=200, content={"status": "ok"})

    # Catch-all proxy route

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy_request(request: Request, path: str) -> Response:  # noqa: C901
        settings: ProxySettings = request.app.state.settings
        repo: ShardRepository = request.app.state.repo
        rules_engine: RulesEngine = request.app.state.rules_engine
        httpx_client: httpx.AsyncClient = request.app.state.httpx_client

        clean_path = "/" + path.split("?")[0].lstrip("/")

        # Validate alias header present, or infer from path
        alias = request.headers.get("x-worthless-key")
        if not alias and settings.allow_alias_inference:
            alias = _infer_alias_from_path(clean_path, settings)
        if not alias:
            return _uniform_401()

        if not _ALIAS_RE.fullmatch(alias):
            return _uniform_401()

        if not settings.allow_insecure:
            proto = request.scope.get("scheme", "http")
            if proto != "https":
                return _uniform_401()

        # Reject null/CR/LF in header keys or values
        for key, value in request.headers.items():
            if _BAD_HEADER_CHARS.intersection(key) or _BAD_HEADER_CHARS.intersection(value):
                return _uniform_401()

        # Fetch encrypted shard (gate-before-decrypt: no Fernet yet)
        encrypted = await repo.fetch_encrypted(alias)
        if encrypted is None:
            return _uniform_401()

        # Load shard_a from header or file fallback
        shard_a_header = request.headers.get("x-worthless-shard-a")
        shard_a: bytearray | None = None
        if shard_a_header:
            try:
                shard_a = bytearray(base64.b64decode(shard_a_header))
            except Exception:
                return _uniform_401()
        else:
            shard_a_path = Path(settings.shard_a_dir) / alias
            try:
                raw = await asyncio.to_thread(shard_a_path.read_bytes)
                shard_a = bytearray(raw)
                del raw
            except FileNotFoundError:
                pass

        if shard_a is None:
            return _uniform_401()

        # Pre-read body ONCE before rules engine (WOR-182: eliminates
        # Starlette body-caching coupling — rules receive bytes, not stream)
        body = await request.body()

        # GATE: rules engine evaluates BEFORE any Fernet decrypt
        denial = await rules_engine.evaluate(alias, request, provider=encrypted.provider, body=body)
        if denial is not None:
            # Zero shard_a before returning
            shard_a[:] = b"\x00" * len(shard_a)
            return Response(
                content=denial.body,
                status_code=denial.status_code,
                headers=denial.headers,
                media_type="application/json",
            )

        # Get adapter (uniform 401, not 404, for anti-enumeration)
        adapter = get_adapter(clean_path)
        if adapter is None:
            shard_a[:] = b"\x00" * len(shard_a)
            return _uniform_401()

        # Decrypt now that the gate has passed
        stored = repo.decrypt_shard(encrypted)

        # Reconstruct key inside secure_key context (body already read above)
        req_headers = {k: v for k, v in request.headers.items()}

        try:
            key_buf = reconstruct_key(shard_a, stored.shard_b, stored.commitment, stored.nonce)
        except Exception:
            shard_a[:] = b"\x00" * len(shard_a)
            stored.zero()
            return _uniform_401()

        # Build and send with stream=True for SSE support
        upstream_resp: httpx.Response | None = None
        try:
            with secure_key(key_buf) as k:
                # Prepare upstream request
                adapter_req = adapter.prepare_request(body=body, headers=req_headers, api_key=k)

                # Build the httpx request object
                upstream_req = httpx_client.build_request(
                    method=request.method,
                    url=adapter_req.url,
                    headers=adapter_req.headers,
                    content=adapter_req.body,
                )

                try:
                    upstream_resp = await httpx_client.send(upstream_req, stream=True)
                except httpx.TimeoutException:
                    return _make_gateway_response(504, "gateway timeout")
                except httpx.ConnectError:
                    return _make_gateway_response(502, "bad gateway")
                except httpx.HTTPError:
                    return _make_gateway_response(502, "bad gateway")

            # Relay response (key_buf is zeroed after secure_key exits)
            adapter_resp = await adapter.relay_response(upstream_resp)

            clean_headers = _strip_worthless_headers(adapter_resp.headers)
            provider = encrypted.provider

            async def _do_record_spend(data: bytes):
                """Extract usage and record spend — shared by streaming and non-streaming."""
                if provider == "anthropic":
                    usage = extract_usage_anthropic(data)
                else:
                    usage = extract_usage_openai(data)
                tokens = usage.total_tokens if usage else 0
                model = usage.model if usage else None
                if usage is None:
                    logger.warning(  # nosemgrep: python-logger-credential-disclosure
                        "Token extraction failed for alias=%s provider=%s",
                        alias,
                        provider,
                    )
                try:
                    await record_spend(settings.db_path, alias, tokens, model, provider)
                except Exception:
                    logger.warning("Failed to record spend for alias=%s", alias)

            if adapter_resp.status_code >= 400:
                sanitized_body = _sanitize_upstream_error(
                    adapter_resp.status_code, adapter_resp.body, provider
                )
                return Response(
                    content=sanitized_body,
                    status_code=adapter_resp.status_code,
                    headers={"content-type": "application/json"},
                    media_type="application/json",
                    background=BackgroundTask(_do_record_spend, adapter_resp.body),
                )

            if adapter_resp.is_streaming and adapter_resp.stream is not None:
                usage_collector = StreamingUsageCollector(provider=encrypted.provider)

                async def _stream_with_metering() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in adapter_resp.stream:  # type: ignore[union-attr]
                            usage_collector.feed(chunk)
                            yield chunk
                    finally:
                        # Client disconnect or stream end: close upstream
                        await upstream_resp.aclose()  # type: ignore[union-attr]

                async def _record_metering():
                    usage = usage_collector.result()
                    if usage is not None:
                        await record_spend(
                            settings.db_path,
                            alias,
                            usage.total_tokens,
                            usage.model,
                            encrypted.provider,
                        )
                    else:
                        # Zero friction: if we can't extract usage (provider
                        # changed SSE format, etc.), log a warning but don't
                        # penalize the user with phantom spend.
                        logger.warning(
                            "Could not extract usage from streaming response "
                            "for alias=%s; spend not recorded",
                            alias,
                        )

                return StreamingResponse(
                    _stream_with_metering(),
                    status_code=adapter_resp.status_code,
                    headers=clean_headers,
                    background=BackgroundTask(_record_metering),
                )
            else:
                await upstream_resp.aclose()

                return Response(
                    content=adapter_resp.body,
                    status_code=adapter_resp.status_code,
                    headers=clean_headers,
                    media_type=clean_headers.get("content-type", "application/json"),
                    background=BackgroundTask(_do_record_spend, adapter_resp.body),
                )
        finally:
            shard_a[:] = b"\x00" * len(shard_a)
            if stored is not None:
                stored.zero()

    return app


def _make_gateway_response(status_code: int, message: str) -> Response:
    """Create a gateway error response (502/504)."""
    err = gateway_error_response(status_code, message)
    return Response(
        content=err.body,
        status_code=err.status_code,
        headers=err.headers,
        media_type="application/json",
    )
