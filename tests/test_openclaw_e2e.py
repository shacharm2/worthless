"""OpenClaw integration test — prove shard-A works end-to-end through Docker Compose.

Two-container stack: mock-upstream + worthless-proxy. The proxy reads
shard-A from disk (written by enroll), reconstructs the real key via XOR,
and forwards to mock-upstream. Tests verify the real key arrives upstream.

This proves any HTTP client (OpenClaw, Cursor, etc.) that sends requests
to the proxy will have its key reconstructed correctly — without ever
needing the real key configured.

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

import httpx
import pytest

from tests.helpers import fake_openai_key

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
ALIAS = "openai-octest1"


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


# ---------------------------------------------------------------------------
# Session-scoped fixture: 2-container stack
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def openclaw_stack():
    """Build and start the mock-upstream + worthless-proxy stack.

    Uses dynamic ports to avoid conflicts. The proxy has alias inference
    enabled so clients don't need x-worthless-key headers.

    Yields (proxy_port, mock_port, fake_key) for test assertions.
    """
    result = subprocess.run(["docker", "info"], capture_output=True)
    if result.returncode != 0:
        pytest.skip("Docker daemon not running")

    project = f"openclaw-e2e-{uuid.uuid4().hex[:8]}"
    fake_key = fake_openai_key()

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

        # 4. Enroll the fake key — writes shard-A to disk + shard-B to DB
        enroll = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                proxy_container,
                "worthless",
                "enroll",
                "--alias",
                ALIAS,
                "--key-stdin",
                "--provider",
                "openai",
            ],
            input=fake_key,
            capture_output=True,
            text=True,
        )
        assert enroll.returncode == 0, f"Enrollment failed: {enroll.stderr}"

        # 5. Clear any captured headers from startup health checks
        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        yield proxy_port, mock_port, fake_key

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
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenClawShardA:
    """Prove the proxy reconstructs the real key and forwards it upstream.

    The proxy reads shard-A from disk (written by enroll), shard-B from
    the DB, XORs them to reconstruct the original API key, and replaces
    the Authorization header before sending to mock-upstream.
    """

    def test_shard_a_reconstructs(self, openclaw_stack):
        """POST to proxy, verify mock-upstream receives the REAL key."""
        proxy_port, mock_port, fake_key = openclaw_stack

        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        resp = httpx.post(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
            },
            headers={
                "x-worthless-key": ALIAS,
                "Content-Type": "application/json",
            },
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
        proxy_port, mock_port, fake_key = openclaw_stack

        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        resp = httpx.post(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
            },
            headers={
                "x-worthless-key": ALIAS,
                "Content-Type": "application/json",
            },
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
        """Raw shard-A bytes never appear in upstream Authorization."""
        proxy_port, mock_port, fake_key = openclaw_stack

        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        httpx.post(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
            },
            headers={
                "x-worthless-key": ALIAS,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        captured = httpx.get(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        ).json()
        for entry in captured["headers"]:
            # The real key must be present, not partial/corrupted
            assert entry["authorization"] == f"Bearer {fake_key}", (
                "Unexpected authorization value at upstream"
            )

    def test_alias_inference(self, openclaw_stack):
        """Proxy reconstructs key via alias inference (no x-worthless-key).

        This simulates what OpenClaw actually does — sends a standard
        request with no Worthless-specific headers.
        """
        proxy_port, mock_port, fake_key = openclaw_stack

        httpx.delete(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        )

        # No x-worthless-key header — pure alias inference
        resp = httpx.post(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "test"}],
            },
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
        assert resp.status_code == 200, f"Alias inference failed: {resp.status_code}: {resp.text}"

        captured = httpx.get(
            f"http://127.0.0.1:{mock_port}/captured-headers",
            timeout=5.0,
        ).json()
        assert len(captured["headers"]) > 0
        upstream_auth = captured["headers"][-1]["authorization"]
        assert f"Bearer {fake_key}" == upstream_auth, (
            "Alias inference did not produce the correct key"
        )
