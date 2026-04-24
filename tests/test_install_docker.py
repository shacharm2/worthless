"""Docker fresh-machine integration tests for install.sh.

Marked 'docker' (excluded from the default pytest run via pyproject.toml).
Run with: pytest -m docker tests/test_install_docker.py

Validates the fresh-box promise: a non-Python Linux box can run install.sh
and end up with a working `worthless` CLI. Also chains the lock lifecycle
against a mock upstream so `worthless --version` isn't the only behavior
proven.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from tests._docker_helpers import docker_available
from tests._install_helpers import INSTALL_FIXTURES, REPO_ROOT

LOCK_E2E_SERVICE = "worthless-installed"
LOCK_E2E_COMPOSE = INSTALL_FIXTURES / "docker-compose.lock-e2e.yml"

# Install matrix — all Supported tier. `linux/amd64` pinned so arm64 Macs
# still exercise amd64 coverage.
INSTALL_MATRIX = [
    "ubuntu-bare",  # 24.04, no python, no uv
    "ubuntu-2204-bare",  # 22.04 LTS — still the prod majority
    "ubuntu-with-uv",  # 24.04 + pre-installed uv (reuse path)
    "debian-12-bare",  # second glibc distro
    "alpine-bare",  # musl — uv fetches musl-compatible Python via PBS
]

# Lock-lifecycle matrix — (distro_label, dockerfile_name). The compose file
# selects the dockerfile via the LOCK_E2E_DOCKERFILE env var.
LOCK_E2E_MATRIX = [
    ("ubuntu-bare", "Dockerfile.ubuntu-bare-lock-e2e"),
    ("debian-12", "Dockerfile.debian-12-lock-e2e"),
]

BUILD_PLATFORM = "linux/amd64"


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker not available"),
]


# Test timeout must exceed the sum of subprocess budgets below
# (build=240 + run=180 + rmi=30) so pytest-timeout doesn't preempt
# a legitimately slow build before it gets a chance to report.
@pytest.mark.timeout(480)
@pytest.mark.parametrize("fixture", INSTALL_MATRIX)
def test_install_succeeds_on_distro(fixture: str) -> None:
    """Build image, run install.sh + verify_install.sh, assert both succeed."""
    dockerfile = INSTALL_FIXTURES / f"Dockerfile.{fixture}"
    # uuid suffix: xdist workers running retries or sibling jobs on the same
    # daemon must not race on a shared image tag.
    image_tag = f"worthless-install-test:{fixture}-{uuid.uuid4().hex[:8]}"
    assert dockerfile.is_file(), f"missing fixture: {dockerfile}"

    try:
        build = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker",
                "build",
                "--platform",
                BUILD_PLATFORM,
                "--file",
                str(dockerfile),
                "--tag",
                image_tag,
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert build.returncode == 0, (
            f"docker build failed for {fixture}:\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
        )

        run = subprocess.run(  # noqa: S603
            ["docker", "run", "--rm", "--platform", BUILD_PLATFORM, image_tag],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert run.returncode == 0, (
            f"install + verify failed inside {fixture} container:\n"
            f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        )
        # verify_install.sh prints 'OK: install verified at ...' on success.
        assert "OK: install verified" in run.stdout, (
            f"verify_install.sh did not complete successfully in {fixture}:\n"
            f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        )
    finally:
        subprocess.run(  # noqa: S603
            ["docker", "rmi", "-f", image_tag],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


# 480s test timeout covers: compose up (360) + logs (30) + down (60)
# with buffer. Otherwise the teardown and log-capture `finally` blocks
# the test was designed around could be preempted before they run.
@pytest.mark.timeout(480)
@pytest.mark.parametrize(("distro", "dockerfile_name"), LOCK_E2E_MATRIX)
def test_lock_lifecycle_end_to_end(distro: str, dockerfile_name: str) -> None:
    """Post-install: `worthless lock` + proxied request → real key at upstream.

    Chains install → lock → `worthless up` → request via proxy → verify
    mock-upstream saw the reconstructed real key. Teardown happens in
    ``finally`` so a timeout or assertion failure still cleans up.

    Parametrized across Ubuntu + Debian to catch glibc-version drift.
    """
    assert LOCK_E2E_COMPOSE.is_file(), f"missing fixture: {LOCK_E2E_COMPOSE}"

    # uuid suffix: xdist workers can share PIDs, so PID alone can collide.
    project = f"worthless-lock-e2e-{distro}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    compose_base = [
        "docker",
        "compose",
        "-f",
        str(LOCK_E2E_COMPOSE),
        "-p",
        project,
    ]
    # Minimal env — avoid leaking arbitrary WORTHLESS_* / provider keys from
    # the host into the compose build context and service env. Compose needs
    # HOME to locate its context cache; fall back to the runner's real HOME.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ["HOME"],
        "LOCK_E2E_DOCKERFILE": dockerfile_name,
    }

    up_stdout, up_stderr, up_rc = "", "", None
    logs_text = ""
    try:
        try:
            up = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    *compose_base,
                    "up",
                    "--build",
                    "--abort-on-container-exit",
                    "--exit-code-from",
                    LOCK_E2E_SERVICE,
                ],
                capture_output=True,
                text=True,
                timeout=360,
                check=False,
                env=env,
            )
            up_stdout, up_stderr, up_rc = up.stdout, up.stderr, up.returncode
        finally:
            # Always pull logs — especially on timeout, when `up` never finished
            # and the captured stdout is empty or partial.
            logs = subprocess.run(  # noqa: S603
                [*compose_base, "logs", "--no-color"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=env,
            )
            logs_text = f"{logs.stdout}\n{logs.stderr}"
    finally:
        subprocess.run(  # noqa: S603
            [*compose_base, "down", "-v", "--remove-orphans"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=env,
        )

    assert up_rc == 0, (
        f"{LOCK_E2E_SERVICE} ({distro}) exited {up_rc}.\n"
        f"--- compose up stdout ---\n{up_stdout}\n"
        f"--- compose up stderr ---\n{up_stderr}\n"
        f"--- service logs ---\n{logs_text}"
    )
