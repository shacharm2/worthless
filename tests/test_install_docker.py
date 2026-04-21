"""Docker fresh-machine integration test for install.sh.

Marked 'docker' (excluded from the default pytest run via pyproject.toml).
Run with: pytest -m docker tests/test_install_docker.py

Validates the fresh-box AC: a non-Python Linux box can run install.sh and
end up with a working `worthless` CLI. Also chains the lock lifecycle
(WOR-235 AC) against a mock upstream so `worthless --version` isn't the
only behavior the fresh-box test proves.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from tests._docker_helpers import docker_available
from tests._install_helpers import INSTALL_FIXTURES, REPO_ROOT

DOCKERFILE = INSTALL_FIXTURES / "Dockerfile.ubuntu-bare"
IMAGE_TAG = "worthless-install-test:ubuntu-bare"

COMPOSE_LOCK_E2E = INSTALL_FIXTURES / "docker-compose.lock-e2e.yml"
LOCK_E2E_SERVICE = "worthless-installed"


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker not available"),
    pytest.mark.timeout(240),
]


def test_bare_ubuntu_install_succeeds() -> None:
    """Build bare-Ubuntu image, run install.sh, assert `worthless --version` works.

    This is the AC test. Slow (~60-120s including image build + uv + Python download).
    """
    assert DOCKERFILE.is_file(), f"missing fixture: {DOCKERFILE}"

    build = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "build",
            "--file",
            str(DOCKERFILE),
            "--tag",
            IMAGE_TAG,
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert build.returncode == 0, (
        f"docker build failed:\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )

    run = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", IMAGE_TAG],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert run.returncode == 0, (
        f"install.sh failed inside bare-Ubuntu container:\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert "worthless" in run.stdout.lower(), (
        f"`worthless --version` did not produce expected output:\n{run.stdout}"
    )


@pytest.mark.timeout(360)
def test_bare_ubuntu_lock_lifecycle_end_to_end() -> None:
    """Post-install: `worthless lock` + proxied request → real key at upstream.

    Closes the WOR-235 AC gap: the fresh-install test above only proves
    `--version` works, not the product feature users actually care about.
    This chains install → lock → `worthless up` → request via proxy →
    verify mock-upstream saw the reconstructed real key. The stack is
    torn down via ``docker compose down -v`` in every exit path.
    """
    assert COMPOSE_LOCK_E2E.is_file(), f"missing fixture: {COMPOSE_LOCK_E2E}"

    project = f"worthless-lock-e2e-{os.getpid()}"
    compose_base = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_LOCK_E2E),
        "-p",
        project,
    ]

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
        )

        logs = subprocess.run(  # noqa: S603
            [*compose_base, "logs", "--no-color"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        # Always tear down, even on timeout / assertion failure.
        subprocess.run(  # noqa: S603
            [*compose_base, "down", "-v", "--remove-orphans"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    assert up.returncode == 0, (
        f"{LOCK_E2E_SERVICE} exited {up.returncode}.\n"
        f"--- compose up stdout ---\n{up.stdout}\n"
        f"--- compose up stderr ---\n{up.stderr}\n"
        f"--- service logs ---\n{logs.stdout}\n{logs.stderr}"
    )
