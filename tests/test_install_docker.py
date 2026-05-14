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
    "ubuntu-nonroot",  # 24.04 + non-root user, no sudo (WOR-318)
    "ubuntu-idempotency",  # 24.04, runs install.sh twice → expects no-op (WOR-317)
    "debian-12-bare",  # second glibc distro
    "alpine-bare",  # musl — uv fetches musl-compatible Python via PBS
]

# Per-fixture success marker. verify_install.sh (used by every fixture
# that exercises the fresh-box install AC) prints "OK: install verified".
# verify_idempotency.sh (WOR-317) prints "OK: install.sh is idempotent"
# instead — it's a different invariant, different message.
SUCCESS_MARKER = {
    "ubuntu-idempotency": "OK: install.sh is idempotent",
}
DEFAULT_SUCCESS_MARKER = "OK: install verified"

# Lock-lifecycle matrix — (distro_label, dockerfile_name). The compose file
# selects the dockerfile via the LOCK_E2E_DOCKERFILE env var.
LOCK_E2E_MATRIX = [
    ("ubuntu-bare", "Dockerfile.ubuntu-bare-lock-e2e"),
    ("debian-12", "Dockerfile.debian-12-lock-e2e"),
]

BUILD_PLATFORM = "linux/amd64"

# Per-step subprocess budgets (seconds). Comments capture WHY each value:
#
# BUILD_S — cold-cache CI runner: pulls pinned base-image digests (~50MB,
#   no shared layer reuse across digests), runs apt-get + install.sh which
#   downloads uv + worthless from PyPI. 240s was tight; bumped on PR #127.
# RUN_S — runs install.sh inside the container; bounded by uv tool install
#   + smoke test on a fresh-box.
# RMI_S — local-only image deletion.
# COMPOSE_UP_S — pulls 2 base images + builds 2 services + runs lock_e2e.py.
# COMPOSE_LOGS_S / COMPOSE_DOWN_S — local docker compose teardown.
# OUTER_BUFFER_S — buffer above sum-of-subprocess-budgets so pytest-timeout
#   doesn't preempt a legitimately slow stage before it can report.
BUILD_S = 480
RUN_S = 180
RMI_S = 30
COMPOSE_UP_S = 600
COMPOSE_LOGS_S = 30
COMPOSE_DOWN_S = 60
OUTER_BUFFER_S = 30

INSTALL_TIMEOUT = BUILD_S + RUN_S + RMI_S + OUTER_BUFFER_S
LOCK_E2E_TIMEOUT = COMPOSE_UP_S + COMPOSE_LOGS_S + COMPOSE_DOWN_S + OUTER_BUFFER_S


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker not available"),
]


@pytest.mark.timeout(INSTALL_TIMEOUT)
@pytest.mark.parametrize("fixture", INSTALL_MATRIX)
def test_install_succeeds_on_distro(fixture: str) -> None:
    """Build image, run install.sh + verify_install.sh, assert both succeed."""
    dockerfile = INSTALL_FIXTURES / f"Dockerfile.{fixture}"
    # uuid suffix: xdist workers running retries or sibling jobs on the same
    # daemon must not race on a shared image tag.
    image_tag = f"worthless-install-test:{fixture}-{uuid.uuid4().hex[:8]}"
    assert dockerfile.is_file(), f"missing fixture: {dockerfile}"

    try:
        # DOCKER_BUILDKIT=1 forces the BuildKit frontend so RUN --mount=type=cache
        # directives in the fixtures (WOR-320) actually cache uv downloads
        # between matrix runs. Modern docker defaults to BuildKit, but older
        # CI runners and some daemon configs still fall back to the legacy
        # builder, which silently strips --mount.
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
            timeout=BUILD_S,
            check=False,
            env={**os.environ, "DOCKER_BUILDKIT": "1"},
        )
        assert build.returncode == 0, (
            f"docker build failed for {fixture}:\nstdout:\n{build.stdout}\nstderr:\n{build.stderr}"
        )

        run = subprocess.run(  # noqa: S603
            ["docker", "run", "--rm", "--platform", BUILD_PLATFORM, image_tag],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=RUN_S,
            check=False,
        )
        assert run.returncode == 0, (
            f"install + verify failed inside {fixture} container:\n"
            f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        )
        # Each fixture's verify script emits an "OK: …" marker on success.
        # See SUCCESS_MARKER for the per-fixture overrides.
        marker = SUCCESS_MARKER.get(fixture, DEFAULT_SUCCESS_MARKER)
        assert marker in run.stdout, (
            f"verify script did not emit '{marker}' in {fixture}:\n"
            f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        )
    finally:
        subprocess.run(  # noqa: S603
            ["docker", "rmi", "-f", image_tag],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=RMI_S,
            check=False,
        )


@pytest.mark.timeout(LOCK_E2E_TIMEOUT)
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
    # DOCKER_BUILDKIT=1 + COMPOSE_DOCKER_CLI_BUILD=1 ensure the lock-e2e
    # Dockerfiles' cache mounts (WOR-320) are honored on older daemons.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ["HOME"],
        "LOCK_E2E_DOCKERFILE": dockerfile_name,
        "DOCKER_BUILDKIT": "1",
        "COMPOSE_DOCKER_CLI_BUILD": "1",
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
                timeout=COMPOSE_UP_S,
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
                timeout=COMPOSE_LOGS_S,
                check=False,
                env=env,
            )
            logs_text = f"{logs.stdout}\n{logs.stderr}"
    finally:
        subprocess.run(  # noqa: S603
            [*compose_base, "down", "-v", "--remove-orphans"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=COMPOSE_DOWN_S,
            check=False,
            env=env,
        )

    assert up_rc == 0, (
        f"{LOCK_E2E_SERVICE} ({distro}) exited {up_rc}.\n"
        f"--- compose up stdout ---\n{up_stdout}\n"
        f"--- compose up stderr ---\n{up_stderr}\n"
        f"--- service logs ---\n{logs_text}"
    )


# ---------------------------------------------------------------------------
# WOR-310 Phase E — production Dockerfile integration smoke tests.
#
# Phases A-D shipped LOGIC (mock-based unit/property/chaos/order tests
# pin syscall correctness). Phase E builds the actual production
# Dockerfile and verifies the image's RUNTIME state — labels exposed,
# two service users present, /run/worthless ownership correct. The
# only proof we have that the image we ship to users actually carries
# the security claim.
#
# Skipped without docker; CI Linux runs them.
# ---------------------------------------------------------------------------


PRODUCTION_DOCKERFILE = REPO_ROOT / "Dockerfile"


@pytest.mark.timeout(BUILD_S + RUN_S + RMI_S + OUTER_BUFFER_S)
def test_production_image_advertises_required_run_flags_label() -> None:
    """``docker inspect`` reports the LABEL Phase B baked in.

    The LABEL is the self-documenting handshake: ``docker inspect`` →
    ``--security-opt=no-new-privileges`` → operator knows what flag the
    security claim depends on. Future Dockerfile drift that drops the
    LABEL silently breaks the contract; this test catches it.
    """
    image_tag = f"worthless-prod-test:label-{uuid.uuid4().hex[:8]}"
    try:
        build = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker",
                "build",
                "--platform",
                BUILD_PLATFORM,
                "--file",
                str(PRODUCTION_DOCKERFILE),
                "--tag",
                image_tag,
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=BUILD_S,
            check=False,
            env={**os.environ, "DOCKER_BUILDKIT": "1"},
        )
        assert build.returncode == 0, (
            f"docker build failed (rc={build.returncode}):\n"
            f"--- stdout ---\n{build.stdout}\n--- stderr ---\n{build.stderr}"
        )
        inspect = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker",
                "inspect",
                "--format",
                '{{ index .Config.Labels "org.worthless.required-run-flags" }}',
                image_tag,
            ],
            capture_output=True,
            text=True,
            timeout=RMI_S,
            check=False,
        )
        assert inspect.returncode == 0, f"docker inspect failed: {inspect.stderr}"
        assert "--security-opt=no-new-privileges" in inspect.stdout, (
            f"WOR-310 E: image LABEL org.worthless.required-run-flags missing the "
            f"--security-opt=no-new-privileges advisory; got {inspect.stdout!r}"
        )
    finally:
        subprocess.run(  # noqa: S603
            ["docker", "rmi", "-f", image_tag],  # noqa: S607
            capture_output=True,
            timeout=RMI_S,
            check=False,
        )


@pytest.mark.timeout(BUILD_S + RUN_S + RMI_S + OUTER_BUFFER_S)
def test_production_image_creates_both_service_users_with_pinned_uids() -> None:
    """``docker run`` confirms worthless-proxy + worthless-crypto exist at runtime.

    Phase B's static test asserted the Dockerfile contains the useradd
    lines; this test BUILDS the image and asks ``id`` for both names.
    Catches a Dockerfile that compiles but shadows the user creation
    (e.g. base-image overlay that removes /etc/passwd entries) — which
    static text inspection wouldn't find.
    """
    image_tag = f"worthless-prod-test:users-{uuid.uuid4().hex[:8]}"
    try:
        build = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker",
                "build",
                "--platform",
                BUILD_PLATFORM,
                "--file",
                str(PRODUCTION_DOCKERFILE),
                "--tag",
                image_tag,
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=BUILD_S,
            check=False,
            env={**os.environ, "DOCKER_BUILDKIT": "1"},
        )
        assert build.returncode == 0, f"build failed: {build.stderr}"

        run = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "sh",
                image_tag,
                "-c",
                # id exits non-zero if the user is missing — AND chain fails
                # fast and surfaces the missing one in stderr.
                "id worthless-proxy && id worthless-crypto && stat -c '%U:%G %a' /run/worthless",
            ],
            capture_output=True,
            text=True,
            timeout=RUN_S,
            check=False,
        )
        assert run.returncode == 0, (
            f"WOR-310 E: image runtime smoke failed (rc={run.returncode}):\n"
            f"--- stdout ---\n{run.stdout}\n--- stderr ---\n{run.stderr}"
        )
        # uid pin from Dockerfile — drift caught here.
        assert "uid=10001(worthless-proxy)" in run.stdout, (
            f"worthless-proxy uid != 10001; got: {run.stdout!r}"
        )
        assert "uid=10002(worthless-crypto)" in run.stdout, (
            f"worthless-crypto uid != 10002; got: {run.stdout!r}"
        )
        assert "root:worthless 770" in run.stdout, (
            f"/run/worthless ownership/mode wrong; got: {run.stdout!r}"
        )
    finally:
        subprocess.run(  # noqa: S603
            ["docker", "rmi", "-f", image_tag],  # noqa: S607
            capture_output=True,
            timeout=RMI_S,
            check=False,
        )
