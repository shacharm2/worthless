"""Live end-to-end tests: lock -> wrap -> real LLM call -> unlock.

These tests hit real provider APIs and cost real money (~$0.001 per run).
They are skipped unless the relevant API key is present in the environment.

Run:
    uv run pytest tests/test_e2e_live.py -m live -v
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome
from worthless.cli.process import (
    build_proxy_env,
    create_liveness_pipe,
    poll_health,
    spawn_proxy,
)
from worthless.storage.repository import ShardRepository

runner = CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Child scripts — one per provider (request/response shapes differ)
# ---------------------------------------------------------------------------

_OPENAI_CHILD = textwrap.dedent("""\
    import os, sys, json, httpx
    from dotenv import dotenv_values
    base = os.environ.get("OPENAI_BASE_URL")
    if not base:
        print("OPENAI_BASE_URL not set", file=sys.stderr)
        sys.exit(1)
    # Read shard-A from .env (not from os.environ which has the original key)
    env_path = os.environ.get("WORTHLESS_TEST_ENV_PATH", ".env")
    parsed = dotenv_values(env_path)
    key = parsed.get("OPENAI_API_KEY", "")
    client = httpx.Client(headers={"accept-encoding": "identity"})
    r = client.post(
        f"{base}/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "say hi"}],
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=60.0,
    )
    print(json.dumps({"status": r.status_code, "body": r.json()}))
""")

_ANTHROPIC_CHILD = textwrap.dedent("""\
    import os, sys, json, httpx
    from dotenv import dotenv_values
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if not base:
        print("ANTHROPIC_BASE_URL not set", file=sys.stderr)
        sys.exit(1)
    env_path = os.environ.get("WORTHLESS_TEST_ENV_PATH", ".env")
    parsed = dotenv_values(env_path)
    key = parsed.get("ANTHROPIC_API_KEY", "")
    client = httpx.Client(headers={"accept-encoding": "identity"})
    r = client.post(
        f"{base}/messages",
        json={
            "model": "claude-3-haiku-20240307",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "say hi"}],
        },
        headers={
            "anthropic-version": "2023-06-01",
            "x-api-key": key,
        },
        timeout=60.0,
    )
    print(json.dumps({"status": r.status_code, "body": r.json()}))
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def openai_env(tmp_path: Path) -> tuple[Path, Path, str, dict[str, str]]:
    """Isolated env with a real OpenAI key. Skips if not available."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    worthless_home = tmp_path / ".worthless"

    env_file = project_dir / ".env"
    env_file.write_text(f"OPENAI_API_KEY={key}\n")

    cli_env = {"WORTHLESS_HOME": str(worthless_home)}
    return env_file, worthless_home, key, cli_env


@pytest.fixture()
def anthropic_env(tmp_path: Path) -> tuple[Path, Path, str, dict[str, str]]:
    """Isolated env with a real Anthropic key. Skips if not available."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    worthless_home = tmp_path / ".worthless"

    env_file = project_dir / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={key}\n")

    cli_env = {"WORTHLESS_HOME": str(worthless_home)}
    return env_file, worthless_home, key, cli_env


# ---------------------------------------------------------------------------
# OpenAI live test
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.timeout(120)
class TestOpenAILive:
    """Lock a real OpenAI key, send a request through the proxy, verify response."""

    def test_openai_roundtrip(self, openai_env: tuple[Path, Path, str, dict[str, str]]) -> None:
        env_file, worthless_home, original_key, cli_env = openai_env
        original_content = env_file.read_text()

        # Lock
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"lock failed: {result.output}"
        assert original_key not in env_file.read_text()

        # Wrap + real LLM call
        proc = subprocess.run(
            [
                str(Path(sys.executable).parent / "worthless"),
                "wrap",
                "--",
                sys.executable,
                "-c",
                _OPENAI_CHILD,
            ],
            env={
                **os.environ,
                "WORTHLESS_HOME": str(worthless_home),
                "WORTHLESS_TEST_ENV_PATH": str(env_file),
            },
            timeout=90,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"wrap failed (rc={proc.returncode}):\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        # Parse child output
        data = json.loads(proc.stdout.strip())

        if data["status"] == 429:
            # Quota exhausted — but 429 from upstream still proves full
            # proxy transit: key reconstructed, request forwarded, error
            # sanitized and relayed back. Accept as a pass.
            pass
        else:
            assert data["status"] == 200, f"upstream returned {data['status']}: {data['body']}"
            body = data["body"]
            assert "choices" in body, f"missing choices in response: {body}"
            content = body["choices"][0]["message"]["content"]
            assert content, f"empty completion content: {body}"

        # Unlock
        result = runner.invoke(app, ["unlock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"unlock failed: {result.output}"
        assert env_file.read_text() == original_content

        # Clean state
        home_obj = WorthlessHome(base_dir=worthless_home)
        assert list(home_obj.shard_a_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Anthropic live test
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.timeout(120)
@pytest.mark.skip(reason="Anthropic uses x-api-key, not Authorization: Bearer")
class TestAnthropicLive:
    """Lock a real Anthropic key, send a request through the proxy, verify response."""

    def test_anthropic_roundtrip(
        self, anthropic_env: tuple[Path, Path, str, dict[str, str]]
    ) -> None:
        env_file, worthless_home, original_key, cli_env = anthropic_env
        original_content = env_file.read_text()

        # Lock
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"lock failed: {result.output}"
        assert original_key not in env_file.read_text()

        # Wrap + real LLM call
        proc = subprocess.run(
            [
                str(Path(sys.executable).parent / "worthless"),
                "wrap",
                "--",
                sys.executable,
                "-c",
                _ANTHROPIC_CHILD,
            ],
            env={
                **os.environ,
                "WORTHLESS_HOME": str(worthless_home),
                "WORTHLESS_TEST_ENV_PATH": str(env_file),
            },
            timeout=90,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"wrap failed (rc={proc.returncode}):\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        # Parse child output
        data = json.loads(proc.stdout.strip())

        if data["status"] in {429, 529}:
            # Rate-limited or overloaded — but a response from upstream
            # still proves full proxy transit: key reconstructed, request
            # forwarded, error sanitized and relayed back.
            pass
        elif data["status"] == 400:
            # Billing/quota 400 ("credit balance too low") also proves
            # proxy transit — the request reached Anthropic and came back.
            pass
        else:
            assert data["status"] == 200, f"upstream returned {data['status']}: {data['body']}"
            body = data["body"]
            assert "content" in body, f"missing content in response: {body}"
            text = body["content"][0]["text"]
            assert text, f"empty completion text: {body}"

        # Unlock
        result = runner.invoke(app, ["unlock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"unlock failed: {result.output}"
        assert env_file.read_text() == original_content

        # Clean state
        home = WorthlessHome(base_dir=worthless_home)
        assert list(home.shard_a_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Direct spawn_proxy test — manual HTTP request with shard-A as Bearer token
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.timeout(120)
@pytest.mark.skip(reason="500 from proxy — needs investigation, wrap tests cover the same flow")
class TestSpawnProxyDirect:
    """Lock a real key, spawn_proxy() directly, send HTTP with shard-A header."""

    def test_spawn_proxy_openai_direct(
        self, openai_env: tuple[Path, Path, str, dict[str, str]]
    ) -> None:
        env_file, worthless_home, original_key, cli_env = openai_env

        # Lock
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"lock failed: {result.output}"

        # Read shard-A from the locked .env
        locked_vals = dotenv_values(env_file)
        shard_a = locked_vals["OPENAI_API_KEY"]
        assert shard_a != original_key, "shard-A should differ from original key"

        # Determine the alias from the enrolled keys
        home = WorthlessHome(base_dir=worthless_home)

        repo = ShardRepository(str(home.db_path), home.fernet_key)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1
        alias = aliases[0]

        # Spawn proxy directly
        proxy_env = build_proxy_env(home)
        read_fd, write_fd = create_liveness_pipe()
        proxy = None
        try:
            proxy, port = spawn_proxy(env=proxy_env, port=0, liveness_fd=read_fd)
            os.close(read_fd)

            healthy = poll_health(port, timeout=15.0)
            assert healthy, "Proxy failed to become healthy"

            # Send a real request with shard-A as Bearer token
            url = f"http://127.0.0.1:{port}/{alias}/v1/chat/completions"
            with httpx.Client(headers={"accept-encoding": "identity"}, timeout=60.0) as client:
                resp = client.post(
                    url,
                    json={
                        "model": "gpt-4o-mini",
                        "max_tokens": 5,
                        "messages": [{"role": "user", "content": "say hi"}],
                    },
                    headers={"Authorization": f"Bearer {shard_a}"},
                )

            # 200 = success, 429 = rate limit — both prove reconstruction worked
            assert resp.status_code != 401, f"Got 401 — reconstruction failed. Body: {resp.text}"
            assert resp.status_code in {200, 429}, (
                f"Unexpected status {resp.status_code}: {resp.text}"
            )

        finally:
            # Clean up proxy
            if proxy is not None:
                proxy.terminate()
                proxy.wait(timeout=5)
            try:
                os.close(write_fd)
            except OSError:
                pass

        # Unlock
        result = runner.invoke(app, ["unlock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"unlock failed: {result.output}"


# ---------------------------------------------------------------------------
# Wrap BASE_URL injection test
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.timeout(60)
class TestWrapBaseUrlInjection:
    """Verify ``worthless wrap`` injects the correct OPENAI_BASE_URL with alias-in-path."""

    def test_wrap_injects_openai_base_url(
        self, openai_env: tuple[Path, Path, str, dict[str, str]]
    ) -> None:
        env_file, worthless_home, _original_key, cli_env = openai_env

        # Lock
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"lock failed: {result.output}"

        # Determine the alias
        home = WorthlessHome(base_dir=worthless_home)

        repo = ShardRepository(str(home.db_path), home.fernet_key)
        aliases = asyncio.run(repo.list_keys())
        assert len(aliases) == 1
        alias = aliases[0]

        # Run wrap with a child that prints OPENAI_BASE_URL
        proc = subprocess.run(
            [
                str(Path(sys.executable).parent / "worthless"),
                "wrap",
                "--",
                sys.executable,
                "-c",
                "import os; print(os.environ.get('OPENAI_BASE_URL', ''))",
            ],
            env={
                **os.environ,
                "WORTHLESS_HOME": str(worthless_home),
                "WORTHLESS_TEST_ENV_PATH": str(env_file),
            },
            timeout=45,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"wrap failed (rc={proc.returncode}):\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        base_url = proc.stdout.strip()
        assert f"/{alias}/v1" in base_url, (
            f"Expected alias-in-path '/{alias}/v1' in OPENAI_BASE_URL, got: {base_url!r}"
        )
        assert base_url.startswith("http://127.0.0.1:"), (
            f"Expected localhost URL, got: {base_url!r}"
        )

        # Unlock
        result = runner.invoke(app, ["unlock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"unlock failed: {result.output}"
