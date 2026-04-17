"""Proxy HTTP surface contract tests.

These tests start the real proxy with a mock upstream and hit it over HTTP,
validating the external interface contract that TestSprite confirmed works.
They run in CI and break the build if the contract is violated.

Requires: the proxy + mock upstream to be running (handled by the
``live_proxy`` fixture which reuses the harness machinery).
"""

from __future__ import annotations

import asyncio
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography.fernet import Fernet

from tests.helpers import fake_anthropic_key, fake_openai_key
from worthless.crypto.splitter import split_key_fp
from worthless.proxy.app import create_app
from worthless.proxy.config import ProxySettings
from worthless.storage.repository import ShardRepository, StoredShard

# Import mock app from harness (same mock upstream)
import worthless.adapters.anthropic as _anth_mod
import worthless.adapters.openai as _oai_mod

# Inline the mock app to avoid importing scripts/ with sys.path hacks
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


MOCK_OPENAI_RESPONSE = {
    "id": "chatcmpl-contract-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "contract test"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

MOCK_ANTHROPIC_RESPONSE = {
    "id": "msg-contract-test",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "contract test"}],
    "model": "claude-3-5-sonnet-20241022",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 5, "output_tokens": 3},
}


async def _mock_openai(request: Request) -> JSONResponse:
    return JSONResponse(MOCK_OPENAI_RESPONSE)


async def _mock_anthropic(request: Request) -> JSONResponse:
    return JSONResponse(MOCK_ANTHROPIC_RESPONSE)


async def _mock_catchall(request: Request) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": f"Unknown: {request.url.path}", "type": "invalid_request_error"}},
        status_code=404,
    )


_mock_app = Starlette(
    routes=[
        Route("/v1/chat/completions", _mock_openai, methods=["POST"]),
        Route("/v1/messages", _mock_anthropic, methods=["POST"]),
        Route("/{path:path}", _mock_catchall),
    ]
)


def _free_port() -> int:
    """Return an OS-assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_proxy():
    """Start mock upstream + real proxy, yield base URL, tear down."""
    tmpdir = tempfile.mkdtemp(prefix="worthless-contract-")
    db_path = str(Path(tmpdir) / "worthless.db")
    fernet_key = Fernet.generate_key()

    mock_port = _free_port()
    proxy_port = _free_port()

    # Enroll fake keys and collect shard_a tokens for Bearer auth
    shard_a_tokens: dict[str, str] = {}

    prefixes = {"openai": "sk-proj-", "anthropic": "sk-ant-api03-"}

    async def _enroll():
        repo = ShardRepository(db_path, fernet_key)
        await repo.initialize()
        for provider, key_fn in [("openai", fake_openai_key), ("anthropic", fake_anthropic_key)]:
            api_key = key_fn()
            prefix = prefixes[provider]
            sr = split_key_fp(api_key, prefix=prefix, provider=provider)
            shard = StoredShard(
                shard_b=bytearray(sr.shard_b),
                commitment=bytearray(sr.commitment),
                nonce=bytearray(sr.nonce),
                provider=provider,
            )
            await repo.store(f"{provider}-contract", shard, prefix=sr.prefix, charset=sr.charset)
            shard_a_tokens[provider] = sr.shard_a.decode("utf-8")

    asyncio.run(_enroll())

    # Save originals before patching — restore in finally to prevent xdist pollution
    _oai_original = _oai_mod.UPSTREAM_URL
    _anth_original = _anth_mod.UPSTREAM_URL

    try:
        # Patch upstream URLs to mock
        mock_upstream = f"http://127.0.0.1:{mock_port}"
        _oai_mod.UPSTREAM_URL = f"{mock_upstream}/v1/chat/completions"
        _anth_mod.UPSTREAM_URL = f"{mock_upstream}/v1/messages"

        # Create proxy app
        settings = ProxySettings(
            db_path=db_path,
            fernet_key=bytearray(fernet_key),
            allow_insecure=True,
            default_rate_limit_rps=100.0,
        )
        proxy_app = create_app(settings)

        # Start both servers in background threads
        mock_server = uvicorn.Server(
            uvicorn.Config(_mock_app, host="127.0.0.1", port=mock_port, log_level="error")
        )
        proxy_server = uvicorn.Server(
            uvicorn.Config(proxy_app, host="127.0.0.1", port=proxy_port, log_level="error")
        )

        mock_thread = threading.Thread(target=mock_server.run, daemon=True)
        proxy_thread = threading.Thread(target=proxy_server.run, daemon=True)
        mock_thread.start()
        proxy_thread.start()

        # Wait for both servers to be ready
        for label, port in [("Mock upstream", mock_port), ("Proxy", proxy_port)]:
            for _ in range(30):
                try:
                    httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
                    break
                except (httpx.ConnectError, httpx.ReadError):
                    time.sleep(0.2)
            else:
                raise RuntimeError(f"{label} did not start within 6 seconds")
        base_url = f"http://127.0.0.1:{proxy_port}"

        yield base_url, shard_a_tokens

        # Teardown
        mock_server.should_exit = True
        proxy_server.should_exit = True
        mock_thread.join(timeout=3)
        proxy_thread.join(timeout=3)
    finally:
        # Always restore upstream URLs — prevents cross-test pollution under xdist
        _oai_mod.UPSTREAM_URL = _oai_original
        _anth_mod.UPSTREAM_URL = _anth_original

        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    def test_root_returns_200(self, live_proxy) -> None:
        base_url, _ = live_proxy
        r = httpx.get(f"{base_url}/")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_healthz_returns_200(self, live_proxy) -> None:
        base_url, _ = live_proxy
        r = httpx.get(f"{base_url}/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_readyz_returns_200(self, live_proxy) -> None:
        base_url, _ = live_proxy
        r = httpx.get(f"{base_url}/readyz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# OpenAI proxy
# ---------------------------------------------------------------------------


class TestOpenAIProxy:
    def test_chat_completions_returns_200(self, live_proxy) -> None:
        base_url, tokens = live_proxy
        r = httpx.post(
            f"{base_url}/openai-contract/v1/chat/completions",
            headers={"authorization": f"Bearer {tokens['openai']}"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        body = r.json()
        assert "choices" in body
        assert "usage" in body

    def test_chat_completions_no_worthless_headers_leaked(self, live_proxy) -> None:
        base_url, tokens = live_proxy
        r = httpx.post(
            f"{base_url}/openai-contract/v1/chat/completions",
            headers={"authorization": f"Bearer {tokens['openai']}"},
            json={"model": "gpt-4", "messages": []},
        )
        assert r.status_code == 200
        for header in r.headers:
            assert not header.lower().startswith("x-worthless-"), (
                f"Internal header leaked: {header}"
            )


# ---------------------------------------------------------------------------
# Anthropic proxy
# ---------------------------------------------------------------------------


class TestAnthropicProxy:
    def test_messages_returns_200(self, live_proxy) -> None:
        base_url, tokens = live_proxy
        r = httpx.post(
            f"{base_url}/anthropic-contract/v1/messages",
            headers={"authorization": f"Bearer {tokens['anthropic']}"},
            json={
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "content" in body
        assert "usage" in body

    def test_messages_no_worthless_headers_leaked(self, live_proxy) -> None:
        base_url, tokens = live_proxy
        r = httpx.post(
            f"{base_url}/anthropic-contract/v1/messages",
            headers={"authorization": f"Bearer {tokens['anthropic']}"},
            json={
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            },
        )
        assert r.status_code == 200
        for header in r.headers:
            assert not header.lower().startswith("x-worthless-"), (
                f"Internal header leaked: {header}"
            )


# ---------------------------------------------------------------------------
# Anti-enumeration (uniform 401)
# ---------------------------------------------------------------------------


class TestAntiEnumeration:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/models"),
            ("GET", "/nonexistent/path"),
            ("POST", "/v1/embeddings"),
            ("GET", "/admin"),
            ("GET", "/.env"),
        ],
    )
    def test_unknown_paths_return_uniform_401(self, live_proxy, method: str, path: str) -> None:
        base_url, _ = live_proxy
        r = httpx.request(method, f"{base_url}{path}")
        assert r.status_code == 401, f"{method} {path} returned {r.status_code}"
        body = r.json()
        assert body["error"]["type"] == "authentication_error"

    def test_all_401s_have_identical_body(self, live_proxy) -> None:
        base_url, _ = live_proxy
        bodies = []
        for path in ["/v1/models", "/nonexistent", "/admin", "/.env"]:
            r = httpx.get(f"{base_url}{path}")
            assert r.status_code == 401
            bodies.append(r.text)
        assert len(set(bodies)) == 1, "401 responses differ -- anti-enumeration broken"


# ---------------------------------------------------------------------------
# Response format
# ---------------------------------------------------------------------------


class TestResponseFormat:
    def test_health_returns_json_content_type(self, live_proxy) -> None:
        base_url, _ = live_proxy
        r = httpx.get(f"{base_url}/healthz")
        assert "application/json" in r.headers.get("content-type", "")

    def test_401_returns_json_content_type(self, live_proxy) -> None:
        base_url, _ = live_proxy
        r = httpx.get(f"{base_url}/v1/models")
        assert r.status_code == 401
        assert "application/json" in r.headers.get("content-type", "")
