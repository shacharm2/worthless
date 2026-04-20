"""OpenClaw integration test — prove shard-A works end-to-end through Docker Compose.

Two-container stack: mock-upstream + worthless-proxy. The client sends
format-preserving shard-A as a Bearer token to /<alias>/v1/chat/completions.
The proxy reconstructs the real key and forwards to mock-upstream.
Tests verify the real key arrives upstream and shard-A never leaks.

Requires Docker daemon running. Skipped when Docker is unavailable.

Run with:
    uv run pytest tests/test_openclaw_e2e.py -x -v -m openclaw
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import anthropic
import httpx
import openai
import pytest

from tests.helpers import fake_anthropic_key, fake_openai_key
from worthless.cli.commands.lock import _make_alias

# ---------------------------------------------------------------------------
# Module-level skip + markers
# ---------------------------------------------------------------------------
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.openclaw,
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.timeout(300),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "tests" / "openclaw" / "docker-compose.yml"


# ---------------------------------------------------------------------------
# Helpers (matching test_docker_e2e.py patterns)
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """Run a command, raise on failure by default."""
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kwargs)


def _run_ok(cmd: list[str]) -> str:
    """Run and return stdout, raise on failure."""
    return _run(cmd).stdout.strip()


def _docker_exec(container: str, cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a command inside a running container."""
    return subprocess.run(
        ["docker", "exec", container, *cmd],
        capture_output=True,
        text=True,
    )


def _wait_healthy(container: str, timeout: float = 90.0) -> bool:
    """Poll container health status until healthy or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                container,
            ],
            capture_output=True,
            text=True,
        )
        status = result.stdout.strip()
        if status == "healthy":
            return True
        if status in ("unhealthy", ""):
            state = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Status}}",
                    container,
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if state != "running":
                return False
        time.sleep(2)
    return False


def _get_host_port(container: str, internal_port: int) -> int:
    """Discover the dynamic host port mapped to a container port."""
    out = _run_ok(["docker", "port", container, str(internal_port)])
    return int(out.rsplit(":", 1)[-1])


def _write_env_to_container(
    container: str, env_content: str, dest: str = "/tmp/.env"
) -> subprocess.CompletedProcess[str]:
    """Write a .env file into a running container."""
    return subprocess.run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"cat > {dest} << 'ENVEOF'\n{env_content}\nENVEOF",
        ],
        capture_output=True,
        text=True,
    )


def _read_env_value(container: str, var_name: str, path: str = "/tmp/.env") -> str:
    """Read a variable value from a .env file inside a container."""
    result = _docker_exec(
        container,
        ["sh", "-c", f"grep '^{var_name}=' {path} | cut -d= -f2-"],
    )
    assert result.returncode == 0, f"Failed to read {var_name}: {result.stderr}"
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Session-scoped fixture: 2-container stack
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def openclaw_stack():
    """Build and start the mock-upstream + worthless-proxy stack.

    Uses `lock` to split the key (matching production flow):
    shard-A ends up in .env, shard-B in the DB.

    Yields (proxy_port, mock_port, fake_key, shard_a, alias).
    """
    result = subprocess.run(["docker", "info"], capture_output=True)
    if result.returncode != 0:
        pytest.skip("Docker daemon not running")

    project = f"openclaw-e2e-{uuid.uuid4().hex[:8]}"
    fake_key = fake_openai_key()
    alias = _make_alias("openai", fake_key)

    try:
        # 1. Build and start the stack
        _run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "-p",
                project,
                "up",
                "-d",
                "--build",
            ],
            cwd=str(REPO_ROOT),
            timeout=240,
        )

        # 2. Wait for worthless-proxy to be healthy
        proxy_container = f"{project}-worthless-proxy-1"
        if not _wait_healthy(proxy_container, timeout=90):
            logs = subprocess.run(
                ["docker", "logs", proxy_container],
                capture_output=True,
                text=True,
            ).stdout
            pytest.fail(f"worthless-proxy did not become healthy.\n{logs}")

        # 3. Discover dynamic host ports
        proxy_port = _get_host_port(proxy_container, 8787)
        mock_container = f"{project}-mock-upstream-1"
        mock_port = _get_host_port(mock_container, 9999)

        # 4. Lock the key — writes shard-A to .env, shard-B to DB
        env_content = f"OPENAI_API_KEY={fake_key}"
        _write_env_to_container(proxy_container, env_content)
        lock = _docker_exec(proxy_container, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock.returncode == 0, f"Lock failed: {lock.stderr}"

        # 5. Read shard-A from .env (lock replaced the real key)
        shard_a = _read_env_value(proxy_container, "OPENAI_API_KEY")
        assert shard_a != fake_key, "Lock did not replace the key in .env"
        assert shard_a.startswith("sk-"), f"Shard-A not format-preserving: {shard_a[:20]}"

        # 6. Clear any captured headers from startup
        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        yield proxy_port, mock_port, fake_key, shard_a, alias

    finally:
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "-p",
                project,
                "down",
                "-v",
                "--remove-orphans",
            ],
            capture_output=True,
            cwd=str(REPO_ROOT),
            timeout=60,
        )


@pytest.fixture(scope="session")
def openclaw_anthropic_alias(openclaw_stack):
    """Enroll a second alias for Anthropic into the already-running stack.

    Depends on openclaw_stack (proxy + mock are up, OpenAI alias enrolled).
    Writes a distinct Anthropic fake key into a separate .env path inside
    the proxy container, runs `worthless lock`, reads shard-A back.

    Yields (fake_key, shard_a, alias).
    """
    proxy_port, _mock_port, _openai_fake_key, _openai_shard_a, _openai_alias = openclaw_stack
    proxy_container = _find_proxy_container()

    fake_key = fake_anthropic_key()
    alias = _make_alias("anthropic", fake_key)

    env_path = "/tmp/.anthropic.env"
    write = _write_env_to_container(proxy_container, f"ANTHROPIC_API_KEY={fake_key}", dest=env_path)
    if write.returncode != 0:
        pytest.skip(f"failed to write anthropic .env: {write.stderr}")

    lock = _docker_exec(proxy_container, ["worthless", "lock", "--env", env_path])
    if lock.returncode != 0:
        pytest.skip(f"anthropic lock failed: {lock.stderr}")

    shard_a = _read_env_value(proxy_container, "ANTHROPIC_API_KEY", path=env_path)
    assert shard_a != fake_key, "lock did not replace anthropic key in .env"
    assert shard_a.startswith("sk-ant-"), f"anthropic shard-A not format-preserving: {shard_a[:30]}"

    yield fake_key, shard_a, alias


def _find_proxy_container() -> str:
    """Locate the running worthless-proxy container from the openclaw project."""
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=worthless-proxy", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    names = [n for n in result.stdout.strip().splitlines() if "openclaw-e2e-" in n]
    assert len(names) == 1, f"expected exactly one openclaw-e2e proxy container, got: {names}"
    return names[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenClawShardA:
    """Prove the proxy reconstructs the real key and forwards it upstream.

    Client sends format-preserving shard-A as Bearer token to
    /<alias>/v1/chat/completions. Proxy reconstructs via modular
    arithmetic and forwards the real key to mock-upstream.
    """

    def test_shard_a_reconstructs(self, openclaw_stack):
        """POST to proxy, verify mock-upstream receives the REAL key."""
        proxy_port, mock_port, fake_key, shard_a, alias = openclaw_stack

        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        resp = httpx.post(
            f"http://127.0.0.1:{proxy_port}/{alias}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
            },
            headers={"Authorization": f"Bearer {shard_a}"},
            timeout=30.0,
        )
        assert resp.status_code == 200, f"Proxy returned {resp.status_code}: {resp.text}"

        captured = httpx.get(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        ).json()
        assert len(captured["headers"]) > 0, "mock-upstream captured no headers"

        upstream_auth = captured["headers"][-1]["authorization"]
        assert f"Bearer {fake_key}" == upstream_auth, (
            f"Expected real key, got: {upstream_auth[:40]}..."
        )

    def test_streaming(self, openclaw_stack):
        """Streaming request reconstructs the real key too."""
        proxy_port, mock_port, fake_key, shard_a, alias = openclaw_stack

        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        resp = httpx.post(
            f"http://127.0.0.1:{proxy_port}/{alias}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
            },
            headers={"Authorization": f"Bearer {shard_a}"},
            timeout=30.0,
        )
        assert resp.status_code == 200, f"Proxy returned {resp.status_code}: {resp.text}"
        assert "data:" in resp.text, f"Expected SSE chunks, got: {resp.text[:200]}"

        captured = httpx.get(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        ).json()
        assert len(captured["headers"]) > 0
        upstream_auth = captured["headers"][-1]["authorization"]
        assert f"Bearer {fake_key}" == upstream_auth

    def test_shard_a_not_leaked_to_upstream(self, openclaw_stack):
        """Shard-A (format-preserving) never appears in upstream headers."""
        proxy_port, mock_port, fake_key, shard_a, alias = openclaw_stack

        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        httpx.post(
            f"http://127.0.0.1:{proxy_port}/{alias}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
            },
            headers={"Authorization": f"Bearer {shard_a}"},
            timeout=30.0,
        )

        captured = httpx.get(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        ).json()
        for entry in captured["headers"]:
            assert shard_a not in entry["authorization"], "Shard-A leaked to upstream!"
            assert entry["authorization"] == f"Bearer {fake_key}", (
                "Unexpected authorization value at upstream"
            )


def _clear_mock_headers(mock_port: int) -> None:
    httpx.delete(f"http://127.0.0.1:{mock_port}/captured-headers", timeout=5.0)


class TestOpenAISDKOpenClaw:
    """Prove the openai Python SDK works drop-in against the containerized proxy + mock."""

    def test_basic_chat_via_openai_sdk(self, openclaw_stack):
        proxy_port, mock_port, _fake_key, shard_a, alias = openclaw_stack
        _clear_mock_headers(mock_port)

        client = openai.OpenAI(
            api_key=shard_a,
            base_url=f"http://127.0.0.1:{proxy_port}/{alias}/v1",
        )
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=16,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert resp.choices, f"no choices in response: {resp}"
        assert resp.choices[0].message.content == "Hello from mock upstream!"

    def test_streaming_via_openai_sdk(self, openclaw_stack):
        proxy_port, mock_port, _fake_key, shard_a, alias = openclaw_stack
        _clear_mock_headers(mock_port)

        client = openai.OpenAI(
            api_key=shard_a,
            base_url=f"http://127.0.0.1:{proxy_port}/{alias}/v1",
        )
        stream = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=16,
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        chunks = list(stream)
        assert chunks, "stream yielded zero chunks — SSE broke through proxy"
        contents = [
            c.choices[0].delta.content for c in chunks if c.choices and c.choices[0].delta.content
        ]
        assert "".join(contents) == "Hello!"

    def test_bad_model_raises_not_found_via_openai_sdk(self, openclaw_stack):
        proxy_port, mock_port, _fake_key, shard_a, alias = openclaw_stack
        _clear_mock_headers(mock_port)

        client = openai.OpenAI(
            api_key=shard_a,
            base_url=f"http://127.0.0.1:{proxy_port}/{alias}/v1",
        )
        with pytest.raises(openai.APIStatusError) as exc:
            client.chat.completions.create(
                model="gpt-does-not-exist-zzz",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        assert exc.value.status_code == 404
        msg = str(exc.value).lower()
        assert "traceback" not in msg
        assert "worthless" not in msg


class TestAnthropicSDKOpenClaw:
    """Prove the anthropic Python SDK works drop-in against the containerized proxy + mock."""

    def test_basic_message_via_anthropic_sdk(self, openclaw_stack, openclaw_anthropic_alias):
        proxy_port, mock_port, _openai_fake, _openai_shard, _openai_alias = openclaw_stack
        _fake_key, shard_a, alias = openclaw_anthropic_alias
        _clear_mock_headers(mock_port)

        client = anthropic.Anthropic(
            api_key=shard_a,
            base_url=f"http://127.0.0.1:{proxy_port}/{alias}",
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert resp.content, f"no content in response: {resp}"
        assert resp.content[0].text == "Hello from mock upstream!"

    def test_streaming_via_anthropic_sdk(self, openclaw_stack, openclaw_anthropic_alias):
        proxy_port, mock_port, _openai_fake, _openai_shard, _openai_alias = openclaw_stack
        _fake_key, shard_a, alias = openclaw_anthropic_alias
        _clear_mock_headers(mock_port)

        client = anthropic.Anthropic(
            api_key=shard_a,
            base_url=f"http://127.0.0.1:{proxy_port}/{alias}",
        )
        texts = []
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=16,
            messages=[{"role": "user", "content": "hi"}],
        ) as stream:
            for text in stream.text_stream:
                texts.append(text)
        assert texts, "Anthropic stream yielded zero text events — SSE broke through proxy"
        assert "".join(texts) == "Hello!"

    def test_bad_model_raises_bad_request_via_anthropic_sdk(
        self, openclaw_stack, openclaw_anthropic_alias
    ):
        proxy_port, mock_port, _openai_fake, _openai_shard, _openai_alias = openclaw_stack
        _fake_key, shard_a, alias = openclaw_anthropic_alias
        _clear_mock_headers(mock_port)

        client = anthropic.Anthropic(
            api_key=shard_a,
            base_url=f"http://127.0.0.1:{proxy_port}/{alias}",
        )
        with pytest.raises(anthropic.BadRequestError) as exc:
            client.messages.create(
                model="claude-does-not-exist-zzz",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
        assert exc.value.status_code == 400
        msg = str(exc.value).lower()
        assert "traceback" not in msg
        assert "worthless" not in msg
