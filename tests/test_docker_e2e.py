"""Docker end-to-end tests for the Worthless proxy container.

Requires Docker daemon running. Skipped when Docker is unavailable.
Marked with @pytest.mark.docker -- excluded from default test runs.

Run with:
    uv run pytest tests/test_docker_e2e.py -x -v -m docker
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Module-level skip + marker
# ---------------------------------------------------------------------------
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.timeout(90),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
IMAGE_TAG = "worthless-test:e2e"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
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


def _wait_healthy(container: str, timeout: float = 20.0) -> bool:
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
        time.sleep(1)
    return False


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _fake_openai_key() -> str:
    """Generate a scanner-safe fake OpenAI key at runtime."""
    try:
        from tests.helpers import fake_openai_key

        return fake_openai_key()
    except ImportError:
        import base64
        import hashlib

        raw = hashlib.sha256(b"test-fixture-seed").digest()
        body = base64.urlsafe_b64encode(raw).decode().rstrip("=")[:48]
        return "sk-" + "proj-" + body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_image() -> str:
    """Build the Docker image once per session."""
    result = subprocess.run(["docker", "info"], capture_output=True)
    if result.returncode != 0:
        pytest.skip("Docker daemon not running")

    _run(
        [
            "docker",
            "build",
            "-t",
            IMAGE_TAG,
            "-f",
            str(DOCKERFILE),
            str(REPO_ROOT),
        ]
    )
    yield IMAGE_TAG  # type: ignore[misc]
    subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True)


@pytest.fixture()
def container(docker_image: str) -> tuple[str, int]:
    """Run a standalone container (single /data volume, no compose)."""
    name = f"worthless-e2e-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:8787",
            "-e",
            "WORTHLESS_ALLOW_INSECURE=true",
            docker_image,
        ]
    )
    try:
        assert _wait_healthy(name), f"Container {name} did not become healthy"
        yield name, port  # type: ignore[misc]
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture()
def persistent_container(docker_image: str) -> tuple[str, int, str]:
    """Container with a named volume that survives stop/start."""
    name = f"worthless-e2e-persist-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    vol = f"worthless-e2e-data-{uuid.uuid4().hex[:8]}"
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:8787",
            "-e",
            "WORTHLESS_ALLOW_INSECURE=true",
            "-v",
            f"{vol}:/data",
            docker_image,
        ]
    )
    try:
        assert _wait_healthy(name), f"Container {name} did not become healthy"
        yield name, port, vol  # type: ignore[misc]
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(
            ["docker", "volume", "rm", "-f", vol],
            capture_output=True,
        )


@pytest.fixture()
def compose_stack(docker_image: str) -> tuple[str, str]:
    """Run via docker-compose for volume separation tests."""
    project = f"worthless-e2e-{uuid.uuid4().hex[:8]}"
    compose_file = REPO_ROOT / "deploy" / "docker-compose.yml"
    env_file = REPO_ROOT / "deploy" / "docker-compose.env"

    created_env = False
    if not env_file.exists():
        env_file.write_text("WORTHLESS_ALLOW_INSECURE=true\n")
        created_env = True

    try:
        _run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "-p",
                project,
                "up",
                "-d",
                "--build",
            ],
            cwd=str(REPO_ROOT),
        )

        container_name = f"{project}-proxy-1"
        assert _wait_healthy(container_name, timeout=30), (
            f"Compose container {container_name} did not become healthy"
        )
        yield project, container_name  # type: ignore[misc]
    finally:
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "-p",
                project,
                "down",
                "-v",
                "--remove-orphans",
            ],
            capture_output=True,
            cwd=str(REPO_ROOT),
        )
        if created_env:
            env_file.unlink(missing_ok=True)


# ===================================================================
# Tier 1: Build
# ===================================================================


class TestBuild:
    """Image build and basic structure."""

    def test_image_builds(self, docker_image: str) -> None:
        """Image fixture succeeds -- proves the Dockerfile is valid."""
        assert docker_image == IMAGE_TAG

    def test_entrypoint_executable(self, docker_image: str) -> None:
        result = _run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "",
                docker_image,
                "test",
                "-x",
                "/entrypoint.sh",
            ]
        )
        assert result.returncode == 0

    def test_runs_as_non_root(self, container: tuple[str, int]) -> None:
        name, _ = container
        result = _docker_exec(name, ["id"])
        assert result.returncode == 0
        assert "worthless" in result.stdout
        # uid should be non-zero
        assert "uid=0" not in result.stdout


# ===================================================================
# Tier 2: Bootstrap
# ===================================================================


class TestBootstrap:
    """First-boot initialization checks."""

    def test_container_starts_healthy(self, container: tuple[str, int]) -> None:
        """Container fixture already asserts healthy -- this is explicit."""
        name, _ = container
        result = _docker_exec(name, ["true"])
        assert result.returncode == 0

    def test_fernet_key_generated(self, container: tuple[str, int]) -> None:
        """Standalone container generates fernet.key in /data."""
        name, _ = container
        result = _docker_exec(name, ["test", "-f", "/data/fernet.key"])
        assert result.returncode == 0, "fernet.key not found in /data"

    def test_db_initialized(self, container: tuple[str, int]) -> None:
        name, _ = container
        result = _docker_exec(
            name,
            [
                "python",
                "-c",
                (
                    "import sqlite3; "
                    "c = sqlite3.connect('/data/worthless.db'); "
                    "tables = [r[0] for r in "
                    'c.execute("SELECT name FROM sqlite_master '
                    "WHERE type='table'\").fetchall()]; "
                    "print(tables)"
                ),
            ],
        )
        assert result.returncode == 0
        assert "shards" in result.stdout

    def test_fernet_key_permissions(self, container: tuple[str, int]) -> None:
        name, _ = container
        # GNU coreutils stat inside Debian slim
        result = _docker_exec(name, ["stat", "-c", "%a", "/data/fernet.key"])
        assert result.returncode == 0
        assert result.stdout.strip() == "400"


# ===================================================================
# Tier 3: Persistence
# ===================================================================


class TestPersistence:
    """Data survives container restart."""

    def test_data_persists_across_restart(self, persistent_container: tuple[str, int, str]) -> None:
        name, _port, _vol = persistent_container

        # Enroll a fake key
        key = _fake_openai_key()
        enroll = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "worthless",
                "enroll",
                "--alias",
                "persist-test",
                "--key-stdin",
                "--provider",
                "openai",
            ],
            input=key,
            capture_output=True,
            text=True,
        )
        assert enroll.returncode == 0, f"enroll failed: {enroll.stderr}"

        # Stop and start (not rm)
        _run(["docker", "stop", name])
        _run(["docker", "start", name])
        assert _wait_healthy(name, timeout=30), "Not healthy after restart"

        # Verify the alias still exists
        status = _docker_exec(
            name,
            [
                "worthless",
                "--json",
                "status",
            ],
        )
        assert status.returncode == 0, f"status failed: {status.stderr}"
        assert "persist-test" in status.stdout


# ===================================================================
# Tier 4: Lifecycle
# ===================================================================


class TestLifecycle:
    """Enroll + proxy health."""

    def test_enroll_and_healthz(self, container: tuple[str, int]) -> None:
        name, port = container

        # Enroll a fake key
        key = _fake_openai_key()
        enroll = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "worthless",
                "enroll",
                "--alias",
                "healthz-test",
                "--key-stdin",
                "--provider",
                "openai",
            ],
            input=key,
            capture_output=True,
            text=True,
        )
        assert enroll.returncode == 0, f"enroll failed: {enroll.stderr}"

        # Hit healthz
        resp = httpx.get(
            f"http://127.0.0.1:{port}/healthz",
            timeout=5.0,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body


# ===================================================================
# Tier 5: Security (compose-specific)
# ===================================================================


class TestComposeSecurity:
    """Compose stack security hardening."""

    def test_compose_fernet_on_data_volume(self, compose_stack: tuple[str, str]) -> None:
        _project, cname = compose_stack
        # fernet.key lives in /data (compose mounts worthless-data:/data)
        result = _docker_exec(cname, ["test", "-f", "/data/fernet.key"])
        assert result.returncode == 0, "fernet.key not in /data"

    def test_compose_read_only_filesystem(self, compose_stack: tuple[str, str]) -> None:
        _project, cname = compose_stack
        result = _docker_exec(cname, ["touch", "/etc/test"])
        assert result.returncode != 0, "Filesystem should be read-only"
        assert "read-only" in result.stderr.lower() or "read only" in result.stderr.lower()

    def test_compose_non_root(self, compose_stack: tuple[str, str]) -> None:
        _project, cname = compose_stack
        result = _docker_exec(cname, ["id"])
        assert result.returncode == 0
        assert "worthless" in result.stdout
        assert "uid=0" not in result.stdout
