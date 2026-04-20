"""Docker fresh-machine integration test for install.sh (WOR-235).

Marked 'docker' (excluded from the default pytest run via pyproject.toml).
Run with: pytest -m docker tests/test_install_docker.py

Validates the WOR-235 acceptance criterion: a fresh non-Python Linux box
can run install.sh and end up with a working `worthless` CLI.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests._install_helpers import INSTALL_FIXTURES, REPO_ROOT

DOCKERFILE = INSTALL_FIXTURES / "Dockerfile.ubuntu-bare"
IMAGE_TAG = "worthless-install-test:ubuntu-bare"


pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def docker_available() -> bool:
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    probe = subprocess.run(  # noqa: S603
        ["docker", "info"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("docker daemon not running")
    return True


def test_bare_ubuntu_install_succeeds(docker_available: bool) -> None:
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
        timeout=300,
        check=False,
    )
    assert build.returncode == 0, (
        f"docker build failed:\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
    )

    run = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", IMAGE_TAG],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, (
        f"install.sh failed inside bare-Ubuntu container:\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
    )
    assert "worthless" in run.stdout.lower(), (
        f"`worthless --version` did not produce expected output:\n{run.stdout}"
    )
