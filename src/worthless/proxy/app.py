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

from worthless.adapters.registry import get_adapter
from worthless.adapters.types import INTERNAL_HEADER_PREFIX
from worthless.crypto.splitter import reconstruct_key, secure_key
from worthless.proxy.config import ProxySettings
from worthless.proxy.errors import auth_error_response, gateway_error_response
from worthless.proxy.metering import extract_usage_anthropic, extract_usage_openai, record_spend
from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule
from worthless.storage.repository import ShardRepository
from worthless.storage.schema import SCHEMA

logger = logging.getLogger(__name__)

_ALIAS_RE = re.compile(r"[a-zA-Z0-9_-]+")


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


def _infer_alias_from_path(clean_path: str, settings: "ProxySettings") -> str | None:
    """Infer alias from request path when x-worthless-key header is absent.

    Maps the path to a provider via the adapter registry, then scans
    shard_a_dir for a unique matching alias (format: ``provider-hash8``).
    Returns None if no match or ambiguous (multiple aliases for same provider).
    """
    from worthless.adapters.registry import get_provider_for_path

    provider = get_provider_for_path(clean_path)
    if not provider:
        return None

    shard_a_dir = Path(settings.shard_a_dir)
    if not shard_a_dir.exists():
        return None

    matches = [
        f.name for f in shard_a_dir.iterdir()
        if f.is_file() and f.name.startswith(f"{provider}-")
    ]
    if len(matches) == 1:
        return matches[0]

    # Zero or multiple matches — cannot infer unambiguously
    if len(matches) > 1:
        logger.warning(
            "Ambiguous alias inference: %d aliases for provider %r. "
            "Use x-worthless-key header or enroll only one key per provider.",
            len(matches), provider,
        )
    return None


def _strip_worthless_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove x-worthless-* headers from a response header dict."""
    return {k: v for k, v in headers.items() if not k.lower().startswith(INTERNAL_HEADER_PREFIX)}


def _sanitize_upstream_error(status_code: int, body: bytes, provider: str) -> bytes:
    """Sanitize upstream error response body — strip internal provider details.

    Keeps the status code and error type but replaces the message with a generic
    one to prevent information leakage from the upstream provider.
    """
    try:
        parsed = json.loads(body)
        if provider == "anthropic" and isinstance(parsed, dict):
            error_type = "api_error"
            if "error" in parsed and isinstance(parsed["error"], dict):
                error_type = parsed["error"].get("type", "api_error")
            return json.dumps(
                {
                    "type": "error",
                    "error": {"type": error_type, "message": "upstream provider error"},
                }
            ).encode()
        elif isinstance(parsed, dict) and "error" in parsed:
            error_type = "api_error"
            if isinstance(parsed["error"], dict):
                error_type = parsed["error"].get("type", "api_error")
            return json.dumps(
                {
                    "error": {
                        "message": "upstream provider error",
                        "type": error_type,
                        "param": None,
                        "code": None,
                    }
                }
            ).encode()
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    # Fallback: generic error body
    return json.dumps(
        {
            "error": {
                "message": "upstream provider error",
                "type": "api_error",
                "param": None,
                "code": None,
            }
        }
    ).encode()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the proxy."""
    settings: ProxySettings = app.state.settings

    # Initialize persistent DB connection (H-6/H-7: reuse across requests)
    db = await aiosqlite.connect(settings.db_path)
    await db.executescript(SCHEMA)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.commit()
    app.state.db = db

    # Initialize repository
    repo = ShardRepository(settings.db_path, settings.fernet_key.encode())
    await repo.initialize()
    app.state.repo = repo

    # Initialize httpx client (follow_redirects=False for security)
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

    # Initialize rules engine with persistent DB connection
    rules_engine = RulesEngine(
        rules=[
            SpendCapRule(db=db),
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

    # ---- Middleware stack (reverse order: last registered runs first) ----
    # M-11: CORS denial — no origins allowed, browser-based access blocked
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET"], allow_headers=[])
    # M-1: Body size limit — reject oversized requests before they reach handlers
    from worthless.proxy.middleware import BodySizeLimitMiddleware

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)

    # ---- Health endpoints (no auth) ----

    @app.get("/")
    async def root():
        return {"status": "ok"}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request):
        repo: ShardRepository = request.app.state.repo
        keys = await repo.list_keys()
        if not keys:
            return JSONResponse(status_code=503, content={"status": "no keys enrolled"})
        return {"status": "ok"}

    # ---- Catch-all proxy route ----

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy_request(request: Request, path: str):  # noqa: C901
        settings: ProxySettings = request.app.state.settings
        repo: ShardRepository = request.app.state.repo
        rules_engine: RulesEngine = request.app.state.rules_engine
        httpx_client: httpx.AsyncClient = request.app.state.httpx_client

        # (a) Strip query params from path for adapter lookup
        clean_path = "/" + path.split("?")[0].lstrip("/")

        # (b) Validate alias header present, or infer from path
        alias = request.headers.get("x-worthless-key")
        if not alias:
            alias = _infer_alias_from_path(clean_path, settings)
        if not alias:
            return _uniform_401()

        # (c) Validate alias format (anti-path-traversal)
        if not _ALIAS_RE.fullmatch(alias):
            return _uniform_401()

        # (d) TLS enforcement
        if not settings.allow_insecure:
            proto = request.headers.get("x-forwarded-proto", "http")
            if proto != "https":
                return _uniform_401()

        # (e) Validate no whitespace/null in header keys
        for key in request.headers.keys():
            if any(c in key for c in ("\x00", "\r", "\n")):
                return _uniform_401()

        # (f) Fetch encrypted shard (NO Fernet decrypt — enables gate-before-decrypt)
        encrypted = await repo.fetch_encrypted(alias)
        if encrypted is None:
            return _uniform_401()

        # (g) Load shard_a from header or file fallback
        shard_a_header = request.headers.get("x-worthless-shard-a")
        shard_a: bytearray | None = None
        if shard_a_header:
            try:
                shard_a = bytearray(base64.b64decode(shard_a_header))
            except Exception:
                return _uniform_401()
        else:
            # B-4: Async file I/O with TOCTOU fix (try/except instead of check-then-read)
            shard_a_path = Path(settings.shard_a_dir) / alias
            try:
                raw = await asyncio.to_thread(shard_a_path.read_bytes)
                shard_a = bytearray(raw)
                del raw  # Remove immutable bytes reference sooner
            except FileNotFoundError:
                pass

        if shard_a is None:
            return _uniform_401()

        # (h) GATE: rules engine evaluates BEFORE any Fernet decrypt (SR-03 / CRYP-05)
        denial = await rules_engine.evaluate(alias, request, provider=encrypted.provider)
        if denial is not None:
            # Zero shard_a before returning — gate denied but shard_a was loaded
            shard_a[:] = b"\x00" * len(shard_a)
            return Response(
                content=denial.body,
                status_code=denial.status_code,
                headers=denial.headers,
                media_type="application/json",
            )

        # (i) Get adapter — H-2/M-3: return uniform 401 (not 404) for anti-enumeration
        adapter = get_adapter(clean_path)
        if adapter is None:
            shard_a[:] = b"\x00" * len(shard_a)
            return _uniform_401()

        # (j) NOW decrypt (gate passed) — Fernet decrypt only happens after rules pass
        stored = repo.decrypt_shard(encrypted)

        # (k) Reconstruct key inside secure_key context
        body = await request.body()
        req_headers = {k: v for k, v in request.headers.items()}

        try:
            key_buf = reconstruct_key(shard_a, stored.shard_b, stored.commitment, stored.nonce)
        except Exception:
            # Zero shard material on failure
            shard_a[:] = b"\x00" * len(shard_a)
            stored.zero()
            return _uniform_401()

        # B-1: Build and send with stream=True for SSE support
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

                # NOTE: api_key.decode() in adapter creates an immutable str copy.
                # This is a known PoC limitation — the Rust reconstruction service
                # will handle key material entirely in-process without string copies.

                # H-1: Send with error handling for httpx exceptions
                try:
                    upstream_resp = await httpx_client.send(upstream_req, stream=True)
                except httpx.TimeoutException:
                    return _make_gateway_response(504, "gateway timeout")
                except httpx.ConnectError:
                    return _make_gateway_response(502, "bad gateway")
                except httpx.HTTPError:
                    return _make_gateway_response(502, "bad gateway")

            # secure_key exited — key_buf is zeroed. Stream reads happen after key is gone.

            # Relay response
            adapter_resp = await adapter.relay_response(upstream_resp)

            # Strip x-worthless-* from response headers
            clean_headers = _strip_worthless_headers(adapter_resp.headers)
            provider = encrypted.provider

            # M-4: Sanitize upstream error bodies
            if adapter_resp.status_code >= 400:
                sanitized_body = _sanitize_upstream_error(
                    adapter_resp.status_code, adapter_resp.body, provider
                )
                return Response(
                    content=sanitized_body,
                    status_code=adapter_resp.status_code,
                    headers={"content-type": "application/json"},
                    media_type="application/json",
                )

            if adapter_resp.is_streaming and adapter_resp.stream is not None:
                # B-1: SSE streaming with metering and cleanup
                collected_chunks: list[bytes] = []

                async def _stream_with_metering() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in adapter_resp.stream:  # type: ignore[union-attr]
                            collected_chunks.append(chunk)
                            yield chunk
                    finally:
                        # Client disconnect or stream end: close upstream
                        await upstream_resp.aclose()  # type: ignore[union-attr]

                async def _record_metering():
                    full_data = b"".join(collected_chunks)
                    if provider == "anthropic":
                        tokens = extract_usage_anthropic(full_data)
                    else:
                        tokens = extract_usage_openai(full_data)
                    if tokens > 0:
                        # M-9/M-10: Metering resilience
                        try:
                            await record_spend(settings.db_path, alias, tokens, None, provider)
                        except Exception:
                            logger.warning("Failed to record spend for alias=%s", alias)

                return StreamingResponse(
                    _stream_with_metering(),
                    status_code=adapter_resp.status_code,
                    headers=clean_headers,
                    background=BackgroundTask(_record_metering),
                )
            else:
                # Non-streaming: read body, close response, extract usage
                await upstream_resp.aclose()

                if provider == "anthropic":
                    tokens = extract_usage_anthropic(adapter_resp.body)
                else:
                    tokens = extract_usage_openai(adapter_resp.body)

                async def _record_nonstream_metering():
                    try:
                        await record_spend(settings.db_path, alias, tokens, None, provider)
                    except Exception:
                        logger.warning("Failed to record spend for alias=%s", alias)

                return Response(
                    content=adapter_resp.body,
                    status_code=adapter_resp.status_code,
                    headers=clean_headers,
                    media_type=clean_headers.get("content-type", "application/json"),
                    background=BackgroundTask(_record_nonstream_metering) if tokens > 0 else None,
                )
        finally:
            # B-3: Zero all shard material after request completes
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
