"""Integration tests for the Worthless proxy — gate-before-reconstruct pipeline.

Tests prove the three architectural invariants:
1. Gate-before-reconstruct (CRYP-05): rules engine runs BEFORE reconstruct_key
2. Transparent routing (PROX-04): correct upstream URL/headers per provider
3. Server-side reconstruction (PROX-05): key never in response
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from worthless.proxy.app import create_app
from worthless.proxy.config import ProxySettings
from worthless.proxy.errors import ErrorResponse


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def proxy_settings(tmp_db_path: str, fernet_key: bytes, tmp_path) -> ProxySettings:
    """ProxySettings pointing at a temp DB with insecure mode on (no TLS needed)."""
    shard_a_dir = str(tmp_path / "shard_a")
    return ProxySettings(
        db_path=tmp_db_path,
        fernet_key=fernet_key.decode(),
        default_rate_limit_rps=100.0,
        upstream_timeout=10.0,
        streaming_timeout=30.0,
        allow_insecure=True,
        shard_a_dir=shard_a_dir,
        allow_alias_inference=True,
    )


@pytest.fixture()
async def enrolled_alias(repo, proxy_settings: ProxySettings, sample_api_key_bytes: bytes):
    """Enroll a test key and return (alias, shard_a_b64, raw_api_key)."""
    from worthless.crypto import split_key
    from worthless.storage.repository import StoredShard

    alias = "test-key"
    sr = split_key(sample_api_key_bytes)

    shard = StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider="openai",
    )
    await repo.store(alias, shard)

    # Write shard_a to file as fallback
    shard_a_dir = Path(proxy_settings.shard_a_dir)
    shard_a_dir.mkdir(parents=True, exist_ok=True)
    shard_a_path = shard_a_dir / alias
    with shard_a_path.open("wb") as f:
        f.write(bytes(sr.shard_a))

    shard_a_b64 = base64.b64encode(bytes(sr.shard_a)).decode()
    return alias, shard_a_b64, sample_api_key_bytes


@pytest.fixture()
async def proxy_app(proxy_settings: ProxySettings, repo):
    """A proxy app with state pre-initialized (ASGITransport skips lifespan)."""
    import aiosqlite

    from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule

    app = create_app(proxy_settings)
    # Manually set up state since ASGITransport doesn't run lifespan
    db = await aiosqlite.connect(proxy_settings.db_path)
    app.state.db = db
    app.state.repo = repo
    app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
    app.state.rules_engine = RulesEngine(
        rules=[
            SpendCapRule(db=db),
            RateLimitRule(
                default_rps=proxy_settings.default_rate_limit_rps,
                db_path=proxy_settings.db_path,
            ),
        ]
    )
    yield app
    await app.state.httpx_client.aclose()
    await db.close()


@pytest.fixture()
async def proxy_client(proxy_app):
    """httpx.AsyncClient wired to the proxy app via ASGITransport."""
    transport = httpx.ASGITransport(app=proxy_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ------------------------------------------------------------------
# Health endpoints (no auth required)
# ------------------------------------------------------------------


class TestHealthEndpoints:
    async def test_root_returns_ok(self, proxy_client: httpx.AsyncClient):
        resp = await proxy_client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_healthz_returns_ok(self, proxy_client: httpx.AsyncClient):
        resp = await proxy_client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_readyz_returns_503_when_no_keys(
        self, proxy_settings: ProxySettings, tmp_db_path, fernet_key
    ):
        """readyz should return 503 when no keys are enrolled."""
        from worthless.proxy.rules import RulesEngine
        from worthless.storage.repository import ShardRepository

        app = create_app(proxy_settings)
        # Manually set state for ASGITransport
        empty_repo = ShardRepository(tmp_db_path, fernet_key)
        await empty_repo.initialize()
        app.state.repo = empty_repo
        app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
        app.state.rules_engine = RulesEngine(rules=[])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")
            assert resp.status_code == 503
        await app.state.httpx_client.aclose()

    async def test_readyz_returns_200_with_keys(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        resp = await proxy_client.get("/readyz")
        assert resp.status_code == 200


# ------------------------------------------------------------------
# Auth — uniform 401 (anti-enumeration)
# ------------------------------------------------------------------


class TestUniformAuth:
    async def test_missing_alias_header_returns_401(self, proxy_client: httpx.AsyncClient):
        resp = await proxy_client.post("/v1/chat/completions", content=b"{}")
        assert resp.status_code == 401

    async def test_unknown_alias_returns_401(self, proxy_client: httpx.AsyncClient, enrolled_alias):
        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={"x-worthless-key": "nonexistent"},
            content=b"{}",
        )
        assert resp.status_code == 401

    async def test_missing_shard_a_returns_401(
        self, proxy_client: httpx.AsyncClient, proxy_app, enrolled_alias
    ):
        alias, _, _ = enrolled_alias
        # Remove the shard_a file so neither header nor file provides shard_a
        shard_a_path = Path(proxy_app.state.settings.shard_a_dir) / alias
        if shard_a_path.exists():
            shard_a_path.unlink()

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={"x-worthless-key": alias},
            content=b"{}",
        )
        assert resp.status_code == 401

    async def test_all_401_bodies_identical(self, proxy_client: httpx.AsyncClient, enrolled_alias):
        """All auth failure modes must return the same body (anti-enumeration)."""
        # Missing alias
        r1 = await proxy_client.post("/v1/chat/completions", content=b"{}")
        # Unknown alias
        r2 = await proxy_client.post(
            "/v1/chat/completions",
            headers={"x-worthless-key": "nonexistent"},
            content=b"{}",
        )
        assert r1.status_code == r2.status_code == 401
        assert r1.content == r2.content

    async def test_alias_path_traversal_rejected(self, proxy_client: httpx.AsyncClient):
        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={"x-worthless-key": "../../etc/passwd"},
            content=b"{}",
        )
        assert resp.status_code == 401


# ------------------------------------------------------------------
# Gate-before-reconstruct (CRYP-05)
# ------------------------------------------------------------------


class TestGateBeforeReconstruct:
    @pytest.mark.parametrize(
        "status_code, error_body, extra_headers",
        [
            (402, b'{"error": "spend cap exceeded"}', {}),
            (429, b'{"error": "rate limit exceeded"}', {"Retry-After": "1"}),
        ],
        ids=["spend-cap", "rate-limit"],
    )
    @respx.mock
    async def test_denial_skips_reconstruct(
        self, proxy_app, enrolled_alias, status_code, error_body, extra_headers
    ):
        """When rules engine denies, reconstruct_key is never called."""
        alias, shard_a_b64, _ = enrolled_alias

        with patch("worthless.proxy.app.reconstruct_key", wraps=None) as mock_reconstruct:
            proxy_app.state.rules_engine = type(
                "MockEngine",
                (),
                {
                    "evaluate": AsyncMock(
                        return_value=ErrorResponse(
                            status_code=status_code,
                            body=error_body,
                            headers={"content-type": "application/json", **extra_headers},
                        )
                    )
                },
            )()

            transport = httpx.ASGITransport(app=proxy_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    headers={
                        "x-worthless-key": alias,
                        "x-worthless-shard-a": shard_a_b64,
                    },
                    content=b'{"model": "gpt-4", "messages": []}',
                )
            assert resp.status_code == status_code
            mock_reconstruct.assert_not_called()

    @respx.mock
    async def test_rules_pass_then_reconstruct_called(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """When rules engine passes, key IS reconstructed and upstream called."""
        alias, shard_a_b64, _ = enrolled_alias

        # Mock upstream to return a response
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 10}},
            )
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-key": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == 200


# ------------------------------------------------------------------
# Transparent routing (PROX-04)
# ------------------------------------------------------------------


class TestTransparentRouting:
    @respx.mock
    async def test_openai_path_routes_to_openai(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        alias, shard_a_b64, _ = enrolled_alias

        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 5}},
            )
        )

        await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-key": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert route.called

    @respx.mock
    async def test_anthropic_path_routes_to_anthropic(
        self, repo, proxy_settings: ProxySettings, proxy_app
    ):
        """Enroll an Anthropic key and verify routing."""
        from worthless.crypto import split_key
        from worthless.storage.repository import StoredShard

        api_key = b"sk-ant-test-key-12345678901234"
        sr = split_key(api_key)

        shard = StoredShard(
            shard_b=bytearray(sr.shard_b),
            commitment=bytearray(sr.commitment),
            nonce=bytearray(sr.nonce),
            provider="anthropic",
        )
        await repo.store("ant-key", shard)

        shard_a_b64 = base64.b64encode(bytes(sr.shard_a)).decode()

        shard_a_dir = Path(proxy_settings.shard_a_dir)
        shard_a_dir.mkdir(parents=True, exist_ok=True)
        with (shard_a_dir / "ant-key").open("wb") as f:
            f.write(bytes(sr.shard_a))

        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={"content": [], "usage": {"output_tokens": 5}},
            )
        )

        transport = httpx.ASGITransport(app=proxy_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/messages",
                headers={
                    "x-worthless-key": "ant-key",
                    "x-worthless-shard-a": shard_a_b64,
                    "content-type": "application/json",
                },
                content=b'{"model": "claude-3-5-sonnet-20241022", "max_tokens": 10}',
            )
        assert route.called

    async def test_unknown_path_returns_401_anti_enumeration(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Unknown paths return uniform 401, not 404 (H-2/M-3 anti-enumeration)."""
        alias, shard_a_b64, _ = enrolled_alias
        resp = await proxy_client.post(
            "/v1/unknown",
            headers={
                "x-worthless-key": alias,
                "x-worthless-shard-a": shard_a_b64,
            },
            content=b"{}",
        )
        assert resp.status_code == 401


# ------------------------------------------------------------------
# Server-side reconstruction (PROX-05) — key never in response
# ------------------------------------------------------------------


class TestKeyNotInResponse:
    @respx.mock
    async def test_key_not_in_response_headers(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        alias, shard_a_b64, raw_key = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [], "usage": {"total_tokens": 5}},
            )
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-key": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )

        key_str = raw_key.decode()
        # Key must not appear in any response header
        for name, value in resp.headers.items():
            assert key_str not in value, f"Key found in header {name}"
        # Key must not appear in body
        assert key_str.encode() not in resp.content

    @respx.mock
    async def test_worthless_headers_stripped_from_response(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                headers={"x-worthless-internal": "leak", "x-request-id": "abc"},
                json={"choices": [], "usage": {"total_tokens": 5}},
            )
        )

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-key": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )

        for name in resp.headers:
            assert not name.lower().startswith("x-worthless-"), f"Leaked internal header: {name}"


# ------------------------------------------------------------------
# Security
# ------------------------------------------------------------------


class TestSecurity:
    async def test_tls_enforcement_when_not_insecure(
        self, tmp_db_path, fernet_key, repo, enrolled_alias, proxy_settings
    ):
        """Without allow_insecure, non-TLS requests are rejected."""
        from worthless.proxy.rules import RulesEngine

        settings = ProxySettings(
            db_path=tmp_db_path,
            fernet_key=fernet_key.decode(),
            allow_insecure=False,
            shard_a_dir=proxy_settings.shard_a_dir,
        )
        app = create_app(settings)
        # Manually set state
        app.state.repo = repo
        app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
        app.state.rules_engine = RulesEngine(rules=[])

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            alias, shard_a_b64, _ = enrolled_alias
            resp = await client.post(
                "/v1/chat/completions",
                headers={
                    "x-worthless-key": alias,
                    "x-worthless-shard-a": shard_a_b64,
                },
                content=b"{}",
            )
            # Should get uniform 401 (no info leak about TLS requirement)
            assert resp.status_code == 401
        await app.state.httpx_client.aclose()

    async def test_query_params_stripped_for_adapter_lookup(
        self, proxy_client: httpx.AsyncClient, enrolled_alias
    ):
        """Query params should not affect adapter resolution (returns 401 for unknown)."""
        alias, shard_a_b64, _ = enrolled_alias
        resp = await proxy_client.post(
            "/v1/unknown?foo=bar",
            headers={
                "x-worthless-key": alias,
                "x-worthless-shard-a": shard_a_b64,
            },
            content=b"{}",
        )
        assert resp.status_code == 401

    async def test_no_openapi_docs(self, proxy_client: httpx.AsyncClient):
        resp = await proxy_client.get("/docs")
        assert resp.status_code != 200

    async def test_no_redoc(self, proxy_client: httpx.AsyncClient):
        resp = await proxy_client.get("/redoc")
        assert resp.status_code != 200


# ------------------------------------------------------------------
# Settings validation (L-8)
# ------------------------------------------------------------------


class TestSettingsValidation:
    def test_create_app_rejects_missing_fernet_key(self, tmp_path):
        """create_app() should raise ValueError when fernet_key is empty."""
        settings = ProxySettings(
            db_path=str(tmp_path / "test.db"),
            fernet_key="",
            allow_insecure=True,
        )
        with pytest.raises(ValueError, match="WORTHLESS_FERNET_KEY"):
            create_app(settings)


# ------------------------------------------------------------------
# Transparent proxy — alias inference from request path
# ------------------------------------------------------------------


@pytest.fixture()
async def openai_enrolled_proxy(proxy_settings: ProxySettings, repo, sample_api_key_bytes: bytes):
    """Proxy app with an openai key enrolled using the provider-hash alias format."""
    import aiosqlite

    from worthless.crypto import split_key
    from worthless.proxy.rules import RateLimitRule, RulesEngine, SpendCapRule
    from worthless.storage.repository import StoredShard

    alias = "openai-abcd1234"
    sr = split_key(sample_api_key_bytes)
    shard = StoredShard(
        shard_b=bytearray(sr.shard_b),
        commitment=bytearray(sr.commitment),
        nonce=bytearray(sr.nonce),
        provider="openai",
    )
    await repo.store(alias, shard)

    shard_a_dir = Path(proxy_settings.shard_a_dir)
    shard_a_dir.mkdir(parents=True, exist_ok=True)
    with (shard_a_dir / alias).open("wb") as f:
        f.write(bytes(sr.shard_a))

    app = create_app(proxy_settings)
    db = await aiosqlite.connect(proxy_settings.db_path)
    app.state.db = db
    app.state.repo = repo
    app.state.httpx_client = httpx.AsyncClient(follow_redirects=False)
    app.state.rules_engine = RulesEngine(
        rules=[
            SpendCapRule(db=db),
            RateLimitRule(default_rps=100.0, db_path=proxy_settings.db_path),
        ]
    )
    yield app, alias, sr.shard_a
    await app.state.httpx_client.aclose()
    await db.close()


# ------------------------------------------------------------------
# Upstream errors — 502/504 (WOR-75)
# ------------------------------------------------------------------


class TestUpstreamErrors:
    """WOR-75: Proxy returns sanitized errors when upstream fails."""

    @pytest.mark.parametrize(
        "side_effect, expected_status, leaked_text",
        [
            (httpx.ConnectError("Connection refused"), 502, "Connection refused"),
            (httpx.ReadTimeout("Read timed out"), 504, "Read timed out"),
            (httpx.HTTPError("Something broke"), 502, "Something broke"),
        ],
        ids=["connect-error-502", "timeout-504", "generic-502"],
    )
    @respx.mock
    async def test_upstream_error_returns_sanitized_response(
        self,
        proxy_client: httpx.AsyncClient,
        enrolled_alias,
        side_effect: httpx.HTTPError,
        expected_status: int,
        leaked_text: str,
    ):
        """Upstream errors are mapped to correct status and internal details are not leaked."""
        alias, shard_a_b64, _ = enrolled_alias

        respx.post("https://api.openai.com/v1/chat/completions").mock(side_effect=side_effect)

        resp = await proxy_client.post(
            "/v1/chat/completions",
            headers={
                "x-worthless-key": alias,
                "x-worthless-shard-a": shard_a_b64,
                "content-type": "application/json",
            },
            content=b'{"model": "gpt-4", "messages": []}',
        )
        assert resp.status_code == expected_status
        assert "error" in resp.json()
        assert leaked_text not in resp.text


class TestTransparentProxy:
    """Proxy should infer alias from request path when header is absent."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_openai_path_infers_alias(self, openai_enrolled_proxy):
        """Request to /v1/chat/completions without alias header should succeed."""
        app, alias, shard_a = openai_enrolled_proxy

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            resp = await client.post(
                "http://testserver/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
                headers={
                    "x-worthless-shard-a": base64.b64encode(bytes(shard_a)).decode(),
                },
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_path_without_alias_returns_401(self, openai_enrolled_proxy):
        """Unknown path without alias header should 401."""
        app, _, _ = openai_enrolled_proxy

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            resp = await client.get("http://testserver/v1/unknown/endpoint")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @respx.mock
    async def test_explicit_alias_header_preferred(self, openai_enrolled_proxy):
        """Explicit x-worthless-key header should take precedence over inference."""
        app, alias, shard_a = openai_enrolled_proxy

        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": []})
        )

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
            resp = await client.post(
                "http://testserver/v1/chat/completions",
                json={"model": "gpt-4", "messages": []},
                headers={
                    "x-worthless-key": alias,
                    "x-worthless-shard-a": base64.b64encode(bytes(shard_a)).decode(),
                },
            )
        assert resp.status_code == 200
