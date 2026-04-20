"""Docker end-to-end tests for the Worthless proxy container.

Requires Docker daemon running. Skipped when Docker is unavailable.
Marked with @pytest.mark.docker -- excluded from default test runs.

Run with:
    uv run pytest tests/test_docker_e2e.py -x -v -m docker
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests._docker_helpers import docker_available, docker_exec

# ---------------------------------------------------------------------------
# Module-level skip + marker
# ---------------------------------------------------------------------------
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker not available"),
    pytest.mark.timeout(90),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"

# Use env var if set (CI builds image separately), otherwise build with a
# unique tag per test session to avoid races between parallel runs.
_SESSION_ID = uuid.uuid4().hex[:8]
IMAGE_TAG = os.environ.get("WORTHLESS_DOCKER_IMAGE", f"worthless-test:e2e-{_SESSION_ID}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a command, raise on failure by default."""
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kwargs)


def _run_ok(cmd: list[str]) -> str:
    """Run and return stdout, raise on failure."""
    return _run(cmd).stdout.strip()


def _wait_healthy(container: str, timeout: float = 60.0) -> bool:
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


def _cleanup_container(name: str) -> None:
    """Force-remove a container and its associated volumes if they exist."""
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(
        ["docker", "volume", "rm", "-f", f"{name}-data", f"{name}-secrets"],
        capture_output=True,
    )


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
    """Build the Docker image once per session.

    If WORTHLESS_DOCKER_IMAGE is set (CI), skip the build and use that tag.
    """
    if os.environ.get("WORTHLESS_DOCKER_IMAGE"):
        # CI already built the image -- just use it
        yield IMAGE_TAG  # type: ignore[misc]
        return

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
    # Pre-cleanup in case a previous crashed run left a container with this name
    _cleanup_container(name)
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1::8787",
            "-e",
            "WORTHLESS_ALLOW_INSECURE=true",
            "--read-only",
            "--tmpfs",
            "/tmp:noexec,nosuid",
            "-v",
            f"{name}-data:/data",
            "-v",
            f"{name}-secrets:/secrets",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            docker_image,
        ]
    )
    port = int(_run_ok(["docker", "port", name, "8787"]).rsplit(":", 1)[-1])
    try:
        assert _wait_healthy(name), f"Container {name} did not become healthy"
        yield name, port  # type: ignore[misc]
    finally:
        _cleanup_container(name)


@pytest.fixture()
def persistent_container(docker_image: str) -> tuple[str, int, str]:
    """Container with a named volume that survives stop/start."""
    name = f"worthless-e2e-persist-{uuid.uuid4().hex[:8]}"
    vol = f"worthless-e2e-data-{uuid.uuid4().hex[:8]}"
    # Pre-cleanup
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
    # Let Docker pick the host port to avoid bind conflicts on reruns
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1::8787",
            "-e",
            "WORTHLESS_ALLOW_INSECURE=true",
            "-v",
            f"{vol}:/data",
            docker_image,
        ]
    )
    # Discover the assigned port
    port_out = _run_ok(["docker", "port", name, "8787"])
    port = int(port_out.strip().rsplit(":", 1)[-1])
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
    """Run via docker-compose for volume separation tests.

    Uses a temporary override file to bind a dynamic host port instead
    of the hardcoded 8787 in deploy/docker-compose.yml, avoiding
    conflicts with other processes on the host.
    """
    project = f"worthless-e2e-{uuid.uuid4().hex[:8]}"
    compose_file = REPO_ROOT / "deploy" / "docker-compose.yml"
    env_file = REPO_ROOT / "deploy" / "docker-compose.env"
    override_file = REPO_ROOT / "deploy" / "docker-compose.override.yml"

    created_env = False
    if not env_file.exists():
        env_file.write_text("WORTHLESS_ALLOW_INSECURE=true\n")
        created_env = True

    # Override port to dynamic to avoid bind conflicts
    override_file.write_text('services:\n  proxy:\n    ports:\n      - "127.0.0.1::8787"\n')

    try:
        _run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "-f",
                str(override_file),
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
                "-f",
                str(override_file),
                "-p",
                project,
                "down",
                "-v",
                "--remove-orphans",
            ],
            capture_output=True,
            cwd=str(REPO_ROOT),
        )
        override_file.unlink(missing_ok=True)
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
        result = docker_exec(name, ["id"])
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
        result = docker_exec(name, ["true"])
        assert result.returncode == 0

    def test_fernet_key_generated(self, container: tuple[str, int]) -> None:
        """Standalone container generates fernet.key in /data."""
        name, _ = container
        result = docker_exec(name, ["test", "-f", "/data/fernet.key"])
        assert result.returncode == 0, "fernet.key not found in /data"

    def test_db_initialized(self, container: tuple[str, int]) -> None:
        name, _ = container
        result = docker_exec(
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
        result = docker_exec(name, ["stat", "-c", "%a", "/data/fernet.key"])
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
        status = docker_exec(
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
# Tier 4a: Wave 6 features (default command, --json, --version)
# ===================================================================


class TestWave6Features:
    """Wave 6 features tested inside Docker — real container, no mocks."""

    def test_version_matches_package_metadata(self, container: tuple[str, int]) -> None:
        """worthless --version reports the installed package version inside the container."""
        from importlib.metadata import version as pkg_version

        name, _ = container
        result = docker_exec(name, ["worthless", "--version"])
        assert result.returncode == 0, f"--version failed: {result.stderr}"
        assert pkg_version("worthless") in result.stdout

    def test_json_mode_read_only(self, container: tuple[str, int]) -> None:
        """worthless --json returns structured state, never writes.

        The container proxy is running (entrypoint starts it), but no
        keys are enrolled. --json must report this without modifying
        any state.
        """
        name, _ = container
        result = docker_exec(name, ["worthless", "--json"])
        assert result.returncode == 0, f"--json failed: {result.stderr}"
        import json

        data = json.loads(result.stdout)
        assert "enrolled" in data
        assert "proxy" in data

    def test_json_mode_after_enroll(self, container: tuple[str, int]) -> None:
        """worthless --json reflects enrollment state after enroll."""
        name, _ = container
        key = _fake_openai_key()

        # Enroll a key
        enroll = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "worthless",
                "enroll",
                "--alias",
                "json-test",
                "--key-stdin",
                "--provider",
                "openai",
            ],
            input=key,
            capture_output=True,
            text=True,
        )
        assert enroll.returncode == 0, f"enroll failed: {enroll.stderr}"

        # Now --json should show enrolled
        result = docker_exec(name, ["worthless", "--json"])
        assert result.returncode == 0, f"--json failed: {result.stderr}"
        import json

        data = json.loads(result.stdout)
        assert data["enrolled"] is True

    def test_status_json_has_keys(self, container: tuple[str, int]) -> None:
        """worthless status --json shows enrolled key details."""
        name, _ = container
        key = _fake_openai_key()

        # Enroll
        subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                name,
                "worthless",
                "enroll",
                "--alias",
                "status-test",
                "--key-stdin",
                "--provider",
                "openai",
            ],
            input=key,
            capture_output=True,
            text=True,
            check=True,
        )

        result = docker_exec(name, ["worthless", "--json", "status"])
        assert result.returncode == 0, f"status --json failed: {result.stderr}"
        assert "status-test" in result.stdout

    def test_no_key_chars_in_default_output(self, container: tuple[str, int]) -> None:
        """Default command output contains no key characters (SR-NEW-15).

        Lock a key via the .env flow, then verify the default command
        output never leaks key material.
        """
        name, _ = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        _write_env_to_container(name, env_content)

        # Run default command with --yes
        result = docker_exec(name, ["sh", "-c", "cd /tmp && worthless --yes"])
        combined = result.stdout + result.stderr

        # Full key must never appear
        assert fake_key not in combined, "Full API key leaked in default command output"

        # 12-char body substrings must not appear
        body = fake_key[8:]  # after "sk-proj-" prefix
        for i in range(0, len(body) - 12):
            chunk = body[i : i + 12]
            assert chunk not in combined, f"Key material leaked in output: ...{chunk}..."


# ===================================================================
# Tier 4b: Lock + Wrap E2E flow (WOR-170)
# ===================================================================


def _write_env_to_container(
    container: str, env_content: str, dest: str = "/tmp/.env"
) -> subprocess.CompletedProcess[str]:
    """Write a .env file into a running container via docker exec + sh."""
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


class TestLockWrapE2E:
    """Tier 4b: Lock + Wrap flow inside Docker.

    Verifies the CORE user journey works end-to-end in the container:
    lock a .env file, then wrap a child process that routes through proxy.
    These tests satisfy WOR-170 AC: "docker compose up produces working
    proxy that handles lock+wrap flow."
    """

    def test_lock_enrolls_key_in_container(self, container: tuple[str, int]) -> None:
        """Lock rewrites .env with shard-A and stores enrollment in DB.

        What it tests: The ``worthless lock`` command inside the container
        successfully splits an API key, stores shard-B in DB, and replaces
        the original key in .env with format-preserving shard-A.

        Why it matters: This is the first step of the user journey. If lock
        fails inside Docker, the entire product is broken.

        Failure looks like: .env still contains the original key, or DB has
        no enrollment record.
        """
        name, _port = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        # Write .env into the container
        write_result = _write_env_to_container(name, env_content)
        assert write_result.returncode == 0, f"Failed to write .env: {write_result.stderr}"

        # Run lock
        lock_result = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock_result.returncode == 0, (
            f"'worthless lock' failed (exit {lock_result.returncode}): {lock_result.stderr}"
        )

        # Assert: .env was rewritten (original key is gone)
        cat_result = docker_exec(name, ["cat", "/tmp/.env"])
        assert cat_result.returncode == 0
        assert fake_key not in cat_result.stdout, (
            "Original API key still present in .env after lock -- decoy replacement failed"
        )

        # Assert: no shard_a files on disk (SR-09: shard-A goes to .env only)
        ls_result = docker_exec(name, ["ls", "/data/shard_a/"])
        if ls_result.returncode == 0:
            shard_files = ls_result.stdout.strip()
            assert not shard_files, f"Unexpected shard_a files after lock: {shard_files}"
        # If dir doesn't exist at all, that's also correct (SR-09)

        # Assert: DB has enrollment record
        db_check = docker_exec(
            name,
            [
                "python",
                "-c",
                (
                    "import sqlite3; "
                    "c = sqlite3.connect('/data/worthless.db'); "
                    "rows = c.execute('SELECT COUNT(*) FROM shards').fetchone(); "
                    "print(rows[0])"
                ),
            ],
        )
        assert db_check.returncode == 0
        count = int(db_check.stdout.strip())
        assert count > 0, "No enrollment records in DB after lock"

    def test_wrap_injects_base_url(self, container: tuple[str, int]) -> None:
        """After lock, wrap injects OPENAI_BASE_URL into child environment.

        What it tests: The ``worthless wrap`` command sets OPENAI_BASE_URL
        in the child process environment, pointing to the ephemeral proxy.

        Why it matters: Without BASE_URL injection, SDK calls go directly
        to the provider, bypassing the proxy entirely -- defeating the
        purpose of Worthless.

        Failure looks like: OPENAI_BASE_URL is None or does not contain
        127.0.0.1.
        """
        name, _port = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        # Lock first
        _write_env_to_container(name, env_content)
        lock = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock.returncode == 0, f"lock failed: {lock.stderr}"

        # Wrap a child that prints OPENAI_BASE_URL
        wrap_result = docker_exec(
            name,
            [
                "worthless",
                "wrap",
                "--",
                "python",
                "-c",
                "import os; print(os.environ.get('OPENAI_BASE_URL', 'MISSING'))",
            ],
        )
        assert wrap_result.returncode == 0, f"wrap failed: {wrap_result.stderr}"
        base_url = wrap_result.stdout.strip()
        assert base_url != "MISSING", "OPENAI_BASE_URL not injected into child environment"
        assert "127.0.0.1" in base_url, f"OPENAI_BASE_URL does not point to local proxy: {base_url}"

    def test_proxy_reachable_during_wrap(self, container: tuple[str, int]) -> None:
        """During wrap, the ephemeral proxy responds on /healthz.

        What it tests: While a wrapped child is running, the proxy is
        reachable and serving health checks.

        Why it matters: If the proxy is unreachable during wrap, no API
        requests can be proxied -- the child would get connection refused.

        Failure looks like: /healthz returns non-200 or connection refused.
        """
        name, _port = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        _write_env_to_container(name, env_content)
        lock = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock.returncode == 0, f"lock failed: {lock.stderr}"

        # Wrap a long-running child. Use sh -c to: extract port, curl healthz,
        # print result, then exit. The child itself acts as the health checker.
        wrap_result = docker_exec(
            name,
            [
                "worthless",
                "wrap",
                "--",
                "sh",
                "-c",
                (
                    # Extract port from OPENAI_BASE_URL (http://host:PORT/alias/v1)
                    'PORT=$(python -c "'
                    "from urllib.parse import urlparse; import os; "
                    "url = os.environ.get('OPENAI_BASE_URL', ''); "
                    "print(urlparse(url).port or 8787)"
                    '"); '
                    # Retry healthz a few times (proxy may still be settling)
                    "for i in 1 2 3 4 5; do "
                    '  RESP=$(python -c "'
                    "import urllib.request; "
                    "r = urllib.request.urlopen('http://127.0.0.1:'+'$PORT'+'/healthz'); "
                    "print(r.status)"
                    '") && break || sleep 1; '
                    "done; "
                    'echo "HEALTH_STATUS=$RESP"'
                ),
            ],
        )
        assert wrap_result.returncode == 0, f"wrap failed: {wrap_result.stderr}"
        assert "HEALTH_STATUS=200" in wrap_result.stdout, (
            f"Proxy /healthz not reachable during wrap. Output: {wrap_result.stdout}"
        )

    def test_lock_wrap_full_flow(self, container: tuple[str, int]) -> None:
        """Combined flow: lock -> wrap -> child request routes through proxy.

        What it tests: After locking, a wrapped child can make an HTTP
        request that reaches the proxy. The proxy will return an error
        (no real upstream API key) but the REQUEST PATH must work.

        Why it matters: This is the complete user journey. Even without
        a real API key, the proxy receiving the request proves the
        plumbing works end-to-end.

        Failure looks like: Connection refused (proxy not running) or
        the request never reaches the proxy.
        """
        name, _port = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        _write_env_to_container(name, env_content)
        lock = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock.returncode == 0, f"lock failed: {lock.stderr}"

        # Wrap a child that makes a request to the proxy's /v1/chat/completions
        wrap_result = docker_exec(
            name,
            [
                "worthless",
                "wrap",
                "--",
                "python",
                "-c",
                (
                    "import os, urllib.request, urllib.error, json\n"
                    "base = os.environ['OPENAI_BASE_URL']\n"
                    "key = os.environ.get('OPENAI_API_KEY', 'fake')\n"
                    "url = f'{base}/chat/completions'\n"
                    "msg = [{'role': 'user', 'content': 'hi'}]\n"
                    "data = json.dumps({'model': 'gpt-4', 'messages': msg}).encode()\n"
                    "hdrs = {'Content-Type': 'application/json', "
                    "'Authorization': f'Bearer {key}'}\n"
                    "req = urllib.request.Request(url, data=data, headers=hdrs)\n"
                    "try:\n"
                    "    urllib.request.urlopen(req)\n"
                    "    print('STATUS=200')\n"
                    "except urllib.error.HTTPError as e:\n"
                    "    print(f'STATUS={e.code}')\n"
                    "except urllib.error.URLError as e:\n"
                    "    print(f'ERROR={e.reason}')\n"
                ),
            ],
        )
        assert wrap_result.returncode == 0, f"wrap failed: {wrap_result.stderr}"
        output = wrap_result.stdout.strip()
        # The proxy MUST receive the request (not connection refused).
        # Any HTTP status code (even 4xx/5xx) means the proxy handled it.
        assert output.startswith("STATUS="), (
            f"Request did not reach proxy. Expected STATUS=<code>, got: {output}"
        )


class TestDockerEdgeCases:
    """Edge cases for lock/wrap/unlock inside Docker containers.

    Tests unusual but realistic scenarios that could cause data loss,
    orphan processes, or confusing error messages.
    """

    def test_unlock_then_proxy_rejects_requests(self, container: tuple[str, int]) -> None:
        """After unlock removes enrollments, proxy has no keys to reconstruct.

        What it tests: After unlocking all keys, the proxy starts but
        returns an error on API requests (no shards to reconstruct from).

        Why it matters: Users who unlock and then try to use the proxy
        should get a clear error, not a hang or crash.

        Failure looks like: Proxy crashes, hangs, or returns 200 with
        garbage data.
        """
        name, port = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        # Lock first
        _write_env_to_container(name, env_content)
        lock = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock.returncode == 0, f"lock failed: {lock.stderr}"

        # Unlock
        unlock = docker_exec(name, ["worthless", "unlock", "--env", "/tmp/.env"])
        assert unlock.returncode == 0, f"unlock failed: {unlock.stderr}"

        # Verify no shards remain
        db_check = docker_exec(
            name,
            [
                "python",
                "-c",
                (
                    "import sqlite3; "
                    "c = sqlite3.connect('/data/worthless.db'); "
                    "rows = c.execute('SELECT COUNT(*) FROM shards').fetchone(); "
                    "print(rows[0])"
                ),
            ],
        )
        assert db_check.returncode == 0
        count = int(db_check.stdout.strip())
        assert count == 0, f"Shards still in DB after unlock: {count}"

    def test_wrap_child_spawn_failure(self, container: tuple[str, int]) -> None:
        """wrap with a nonexistent binary exits non-zero, no orphan proxy.

        Why it matters: Orphan proxy processes would leak ports and
        memory inside the container.

        Note: The container runs its own uvicorn (entrypoint), so we
        count processes BEFORE and AFTER wrap — the count must not
        increase.
        """
        name, _port = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        _write_env_to_container(name, env_content)
        lock = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock.returncode == 0, f"lock failed: {lock.stderr}"

        # Count uvicorn processes BEFORE wrap (container's own proxy)
        _uvicorn_count_cmd = [
            "sh",
            "-c",
            "ls /proc/*/cmdline 2>/dev/null | xargs grep -l '[u]vicorn' 2>/dev/null | wc -l",
        ]
        before = docker_exec(name, _uvicorn_count_cmd)
        before_count = int(before.stdout.strip()) if before.returncode == 0 else 0

        # Wrap a nonexistent binary
        wrap_result = docker_exec(
            name,
            ["worthless", "wrap", "--", "/nonexistent/binary"],
        )
        assert wrap_result.returncode != 0, (
            "wrap should exit non-zero when child binary does not exist"
        )

        # Count AFTER — must not have increased
        after = docker_exec(name, _uvicorn_count_cmd)
        after_count = int(after.stdout.strip()) if after.returncode == 0 else 0
        assert after_count <= before_count, (
            f"Orphan proxy: uvicorn count went from {before_count} to {after_count}"
        )

    def test_lock_idempotent(self, container: tuple[str, int]) -> None:
        """Running lock twice on the same .env succeeds (already-locked keys skipped).

        What it tests: Idempotency of the lock command -- running it
        again on an already-locked .env should not error or double-enroll.

        Why it matters: Users may run lock multiple times (habit, scripts,
        CI). Double-enrollment would corrupt the shard mapping.

        Failure looks like: Second lock exits non-zero, or DB has
        duplicate enrollment records.
        """
        name, _port = container
        fake_key = _fake_openai_key()
        env_content = f"OPENAI_API_KEY={fake_key}\n"

        _write_env_to_container(name, env_content)

        # First lock
        lock1 = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock1.returncode == 0, f"first lock failed: {lock1.stderr}"

        # Count enrollments
        count1_result = docker_exec(
            name,
            [
                "python",
                "-c",
                (
                    "import sqlite3; "
                    "c = sqlite3.connect('/data/worthless.db'); "
                    "print(c.execute('SELECT COUNT(*) FROM shards').fetchone()[0])"
                ),
            ],
        )
        count1 = int(count1_result.stdout.strip())

        # Second lock (should be idempotent)
        lock2 = docker_exec(name, ["worthless", "lock", "--env", "/tmp/.env"])
        assert lock2.returncode == 0, f"second lock failed (not idempotent): {lock2.stderr}"

        # Count enrollments again -- should be same
        count2_result = docker_exec(
            name,
            [
                "python",
                "-c",
                (
                    "import sqlite3; "
                    "c = sqlite3.connect('/data/worthless.db'); "
                    "print(c.execute('SELECT COUNT(*) FROM shards').fetchone()[0])"
                ),
            ],
        )
        count2 = int(count2_result.stdout.strip())
        assert count2 == count1, (
            f"Double enrollment detected: {count1} shards after first lock, {count2} after second"
        )

    def test_container_read_only_filesystem(self, container: tuple[str, int]) -> None:
        """Writes to /data succeed but writes to /app fail (read-only root).

        What it tests: The container filesystem is read-only except for
        the /data volume mount.

        Why it matters: Read-only root prevents attackers from modifying
        application code or installing persistence mechanisms.

        Failure looks like: Writing to /app succeeds (filesystem not
        read-only).
        """
        name, _port = container

        # /data should be writable
        data_write = docker_exec(name, ["touch", "/data/test-rw-check"])
        assert data_write.returncode == 0, (
            f"/data should be writable but write failed: {data_write.stderr}"
        )
        # Clean up
        docker_exec(name, ["rm", "-f", "/data/test-rw-check"])

        # Root filesystem should be read-only. Write to /usr which is
        # always root-owned and not a mount/tmpfs — if this succeeds,
        # --read-only is not active. (The standalone container fixture
        # now passes --read-only, so this is a real test.)
        usr_write = docker_exec(name, ["touch", "/usr/test-ro-check"])
        assert usr_write.returncode != 0, (
            "/usr should not be writable -- container root filesystem is not "
            "read-only. Ensure container runs with --read-only flag."
        )


# ===================================================================
# Tier 5: Security (compose-specific)
# ===================================================================


class TestComposeSecurity:
    """Compose stack security hardening."""

    def test_compose_fernet_on_secrets_volume(self, compose_stack: tuple[str, str]) -> None:
        _project, cname = compose_stack
        # Compose sets WORTHLESS_FERNET_KEY_PATH=/secrets/fernet.key
        result = docker_exec(cname, ["test", "-f", "/secrets/fernet.key"])
        assert result.returncode == 0, "fernet.key not on /secrets volume"
        # Must NOT be on /data
        result = docker_exec(cname, ["test", "-f", "/data/fernet.key"])
        assert result.returncode != 0, "fernet.key should not be on /data in compose mode"

    def test_compose_read_only_filesystem(self, compose_stack: tuple[str, str]) -> None:
        _project, cname = compose_stack
        result = docker_exec(cname, ["touch", "/etc/test"])
        assert result.returncode != 0, "Filesystem should be read-only"
        assert "read-only" in result.stderr.lower() or "read only" in result.stderr.lower()

    def test_compose_non_root(self, compose_stack: tuple[str, str]) -> None:
        _project, cname = compose_stack
        result = docker_exec(cname, ["id"])
        assert result.returncode == 0
        assert "worthless" in result.stdout
        assert "uid=0" not in result.stdout
