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

LOCK_E2E_SERVICE = "worthless-installed"

# Lock-lifecycle matrix: each entry is (distro_label, compose_file).
# Debian added so "Supported" isn't Ubuntu-only for the actual product feature.
LOCK_E2E_MATRIX = [
    ("ubuntu-bare", INSTALL_FIXTURES / "docker-compose.lock-e2e.yml"),
    ("debian-12", INSTALL_FIXTURES / "docker-compose.lock-e2e-debian.yml"),
]

# (fixture_name, tier). Tier is informational; all `supported` variants must
# pass. `experimental` variants are non-blocking (see install.sh README).
# Pinned to linux/amd64 so arm64 Macs don't silently skip amd64 coverage.
INSTALL_MATRIX = [
    ("ubuntu-bare", "supported"),  # 24.04, no python, no uv
    ("ubuntu-2204-bare", "supported"),  # 22.04 LTS — still the prod majority
    ("ubuntu-with-uv", "supported"),  # 24.04 + pre-installed uv (reuse path)
    ("debian-12-bare", "supported"),  # second glibc distro
    pytest.param(
        "alpine-bare",
        "experimental",
        marks=[
            pytest.mark.experimental,
            # PBS ships no musl Python builds today. When it does, flip strict=True
            # (or drop xfail entirely) to enforce Alpine as Supported.
            pytest.mark.xfail(
                strict=False,
                reason="python-build-standalone has no musl builds; uv install fails on Alpine",
            ),
        ],
    ),
]

BUILD_PLATFORM = "linux/amd64"


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker not available"),
    pytest.mark.timeout(240),
]


@pytest.mark.parametrize(("fixture", "tier"), INSTALL_MATRIX)
def test_install_succeeds_on_distro(fixture: str, tier: str) -> None:
    """Build image per fixture, run install.sh, assert `worthless --version` works.

    Covers the support matrix: bare Ubuntu, Ubuntu+python, Ubuntu+uv,
    Debian 12, Alpine (musl). Slow per-case (~60-120s incl. build + Python fetch).
    """
    dockerfile = INSTALL_FIXTURES / f"Dockerfile.{fixture}"
    image_tag = f"worthless-install-test:{fixture}"
    assert dockerfile.is_file(), f"missing fixture: {dockerfile}"

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
        f"docker build failed for {fixture} ({tier}):\n"
        f"stdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )

    run = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "--platform", BUILD_PLATFORM, image_tag],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert run.returncode == 0, (
        f"install + verify failed inside {fixture} ({tier}) container:\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    # verify_install.sh prints 'OK: install verified at ...' on success.
    assert "OK: install verified" in run.stdout, (
        f"verify_install.sh did not complete successfully in {fixture}:\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )


@pytest.mark.timeout(360)
@pytest.mark.parametrize(("distro", "compose_file"), LOCK_E2E_MATRIX)
def test_lock_lifecycle_end_to_end(distro: str, compose_file) -> None:
    """Post-install: `worthless lock` + proxied request → real key at upstream.

    Closes the WOR-235 AC gap: the fresh-install test above only proves
    `--version` works, not the product feature users actually care about.
    This chains install → lock → `worthless up` → request via proxy →
    verify mock-upstream saw the reconstructed real key. The stack is
    torn down via ``docker compose down -v`` in every exit path.

    Parametrized across Ubuntu + Debian to catch glibc-version drift.
    """
    assert compose_file.is_file(), f"missing fixture: {compose_file}"

    project = f"worthless-lock-e2e-{distro}-{os.getpid()}"
    compose_base = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
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
        f"{LOCK_E2E_SERVICE} ({distro}) exited {up.returncode}.\n"
        f"--- compose up stdout ---\n{up.stdout}\n"
        f"--- compose up stderr ---\n{up.stderr}\n"
        f"--- service logs ---\n{logs.stdout}\n{logs.stderr}"
    )
