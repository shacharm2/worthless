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
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from worthless.adapters.registry import get_adapter
from worthless.adapters.types import INTERNAL_HEADER_PREFIX
from worthless.crypto.splitter import reconstruct_key, secure_key
from worthless.proxy.config import ProxySettings
from worthless.proxy.errors import auth_error_response
from worthless.proxy.metering import extract_usage_anthropic, extract_usage_openai, record_spend
from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule
from worthless.storage.repository import ShardRepository

_ALIAS_RE = re.compile(r"[a-zA-Z0-9_-]+")


def _uniform_401() -> JSONResponse:
    """Return a uniform 401 response (anti-enumeration)."""
    err = auth_error_response()
    return JSONResponse(
        status_code=err.status_code,
        content=None,
        headers=err.headers,
    )


def _make_uniform_401_bytes() -> tuple[bytes, dict[str, str]]:
    """Pre-compute the uniform 401 body so all code paths return the exact same bytes."""
    err = auth_error_response()
    return err.body, err.headers


# Pre-computed uniform response
_AUTH_BODY, _AUTH_HEADERS = _make_uniform_401_bytes()


def _strip_worthless_headers(headers: dict[str, str]) -> dict[str, str]:
    """Remove x-worthless-* headers from a response header dict."""
    return {
        k: v for k, v in headers.items() if not k.lower().startswith(INTERNAL_HEADER_PREFIX)
    }


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the proxy."""
    settings: ProxySettings = app.state.settings

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

    # Initialize rules engine
    rules_engine = RulesEngine(
        rules=[
            SpendCapRule(db_path=settings.db_path),
            RateLimitRule(default_rps=settings.default_rate_limit_rps),
        ]
    )
    app.state.rules_engine = rules_engine

    yield

    # Cleanup
    await client.aclose()


def create_app(settings: ProxySettings | None = None) -> FastAPI:
    """Create the Worthless proxy FastAPI app.

    Args:
        settings: Proxy settings. If None, loads from environment.
    """
    if settings is None:
        settings = ProxySettings()

    app = FastAPI(
        title="worthless-proxy",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = settings

    # ---- Health endpoints (no auth) ----

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
    async def proxy_request(request: Request, path: str):
        settings: ProxySettings = request.app.state.settings
        repo: ShardRepository = request.app.state.repo
        rules_engine: RulesEngine = request.app.state.rules_engine
        httpx_client: httpx.AsyncClient = request.app.state.httpx_client

        # (a) Strip query params from path for adapter lookup
        clean_path = "/" + path.split("?")[0] if not path.startswith("/") else path
        if "?" in clean_path:
            clean_path = clean_path.split("?")[0]
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path

        # (b) Validate alias header present
        alias = request.headers.get("x-worthless-alias")
        if not alias:
            return Response(
                content=_AUTH_BODY,
                status_code=401,
                headers=_AUTH_HEADERS,
                media_type="application/json",
            )

        # (c) Validate alias format (anti-path-traversal)
        if not _ALIAS_RE.fullmatch(alias):
            return Response(
                content=_AUTH_BODY,
                status_code=401,
                headers=_AUTH_HEADERS,
                media_type="application/json",
            )

        # (d) TLS enforcement
        if not settings.allow_insecure:
            proto = request.headers.get("x-forwarded-proto", "http")
            if proto != "https":
                return Response(
                    content=_AUTH_BODY,
                    status_code=401,
                    headers=_AUTH_HEADERS,
                    media_type="application/json",
                )

        # (e) Validate no whitespace/null in header keys
        for key in request.headers.keys():
            if any(c in key for c in ("\x00", "\r", "\n")):
                return Response(
                    content=_AUTH_BODY,
                    status_code=401,
                    headers=_AUTH_HEADERS,
                    media_type="application/json",
                )

        # (f) Load shard_b from repo
        stored = await repo.retrieve(alias)
        if stored is None:
            return Response(
                content=_AUTH_BODY,
                status_code=401,
                headers=_AUTH_HEADERS,
                media_type="application/json",
            )

        # (g) Load shard_a from header or file fallback
        shard_a_header = request.headers.get("x-worthless-shard-a")
        shard_a: bytes | None = None
        if shard_a_header:
            try:
                shard_a = base64.b64decode(shard_a_header)
            except Exception:
                return Response(
                    content=_AUTH_BODY,
                    status_code=401,
                    headers=_AUTH_HEADERS,
                    media_type="application/json",
                )
        else:
            # File fallback
            shard_a_path = Path(settings.shard_a_dir) / alias
            if shard_a_path.is_file():
                shard_a = shard_a_path.read_bytes()

        if shard_a is None:
            return Response(
                content=_AUTH_BODY,
                status_code=401,
                headers=_AUTH_HEADERS,
                media_type="application/json",
            )

        # (h) GATE: rules engine evaluates BEFORE key reconstruction
        denial = await rules_engine.evaluate(alias, request)
        if denial is not None:
            return Response(
                content=denial.body,
                status_code=denial.status_code,
                headers=denial.headers,
                media_type="application/json",
            )

        # (i) Get adapter
        adapter = get_adapter(clean_path)
        if adapter is None:
            return JSONResponse(status_code=404, content={"error": "unknown endpoint"})

        # (j) Reconstruct key inside secure_key context
        body = await request.body()
        req_headers = {k: v for k, v in request.headers.items()}

        try:
            key_buf = reconstruct_key(
                shard_a, stored.shard_b, stored.commitment, stored.nonce
            )
        except Exception:
            return Response(
                content=_AUTH_BODY,
                status_code=401,
                headers=_AUTH_HEADERS,
                media_type="application/json",
            )

        with secure_key(key_buf) as k:
            # (k) Prepare upstream request
            adapter_req = adapter.prepare_request(
                body=body, headers=req_headers, api_key=k
            )

            # (l) Send upstream request (stream=True)
            upstream_resp = await httpx_client.request(
                method=request.method,
                url=adapter_req.url,
                headers=adapter_req.headers,
                content=adapter_req.body,
                extensions={"timeout": {"read": settings.streaming_timeout}},
            )

        # (m) Relay response via adapter
        adapter_resp = await adapter.relay_response(upstream_resp)

        # (o) Strip x-worthless-* from response headers
        clean_headers = _strip_worthless_headers(adapter_resp.headers)

        # (n) Return response
        if adapter_resp.is_streaming and adapter_resp.stream is not None:
            # Streaming: wrap with metering
            collected_chunks: list[bytes] = []

            async def _stream_with_metering() -> AsyncIterator[bytes]:
                async for chunk in adapter_resp.stream:  # type: ignore[union-attr]
                    collected_chunks.append(chunk)
                    yield chunk

            async def _record_metering():
                full_data = b"".join(collected_chunks)
                provider = stored.provider
                if provider == "anthropic":
                    tokens = extract_usage_anthropic(full_data)
                else:
                    tokens = extract_usage_openai(full_data)
                if tokens > 0:
                    await record_spend(
                        settings.db_path, alias, tokens, None, provider
                    )

            return StreamingResponse(
                _stream_with_metering(),
                status_code=adapter_resp.status_code,
                headers=clean_headers,
                background=BackgroundTask(_record_metering),
            )
        else:
            # Non-streaming: extract usage inline
            provider = stored.provider
            if provider == "anthropic":
                tokens = extract_usage_anthropic(adapter_resp.body)
            else:
                tokens = extract_usage_openai(adapter_resp.body)
            if tokens > 0:
                asyncio.create_task(
                    record_spend(settings.db_path, alias, tokens, None, provider)
                )

            return Response(
                content=adapter_resp.body,
                status_code=adapter_resp.status_code,
                headers=clean_headers,
                media_type=clean_headers.get("content-type", "application/json"),
            )

    return app
