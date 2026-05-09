# ruff: noqa: S104, S108, S603, S607
# S603/S607: docker is invoked by partial path on purpose — the test runner
# must find the docker binary on $PATH; pinning a path makes the test
# brittle across OS/CI environments. S104: binding 0.0.0.0 inside an
# isolated docker container with --read-only is intentional. S108: the
# /tmp tmpfs string is a docker mount spec, not a host path.
"""WOR-465 Phase A1: kernel-level integration test for the fernet.key chmod path.

This test is the answer to a sharp question raised on PR #158:
"how did we not have a test finding this out?"

The original A1 implementation chowned fernet.key to ``root:worthless-crypto
0400``. Static-text grep (``test_deploy_static.py``) saw the magic strings
and approved. But ``0400`` with owner ``root`` means **only root** can read —
the worthless-crypto sidecar (the very identity the file is supposed to
belong to) was locked out. task-completion-validator caught it by actually
exec'ing as worthless-crypto. This test enforces that finding at CI time.

Layered with ``test_deploy_static.py`` (string-pattern checks) + this module
(real container, real kernel) we now cover both regression classes:
- the pattern is in the file (static)
- the pattern produces the correct kernel-enforced behavior (this file)

Strategy: A1 alone cannot boot the proxy with ``WORTHLESS_FERNET_IPC_ONLY=1``
because the proxy still reads fernet.key directly until A3 lands. So we
pre-seed ``fernet.key`` in a volume, start the container, let the
entrypoint's synchronous chmod block run (it executes BEFORE start.py
exec's the proxy, so the chmod completes regardless of whether the proxy
later crashes), then inspect the volume via short-lived ephemeral
containers using ``docker run --rm --user``. The proxy's eventual death
is irrelevant — we only care about the post-chmod file state.

Run with:
    uv run pytest tests/test_deploy_chmod_e2e.py -x -v -m docker
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from tests._docker_helpers import docker_available

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="Docker not available"),
    pytest.mark.timeout(180),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
_SESSION_ID = uuid.uuid4().hex[:8]
IMAGE_TAG = os.environ.get("WORTHLESS_DOCKER_IMAGE", f"worthless-test:chmod-{_SESSION_ID}")

# Standard cap set the entrypoint needs for the priv-drop dance and the
# chown/chmod block. Identical to test_docker_e2e.py's container fixture
# so we exercise the same security posture as production.
_CAPS = [
    "--cap-drop=ALL",
    "--cap-add=SETUID",
    "--cap-add=SETGID",
    "--cap-add=SETPCAP",
    "--cap-add=DAC_OVERRIDE",
    "--cap-add=CHOWN",
    "--cap-add=FOWNER",
    "--security-opt=no-new-privileges",
]


@pytest.fixture(scope="module")
def image() -> str:
    """Build the image once for this module; reuse CI image if pre-built."""
    if os.environ.get("WORTHLESS_DOCKER_IMAGE"):
        yield IMAGE_TAG  # type: ignore[misc]
        return
    subprocess.run(  # noqa: S603
        ["docker", "build", "-t", IMAGE_TAG, "-f", str(DOCKERFILE), str(REPO_ROOT)],  # noqa: S607
        check=True,
        capture_output=True,
    )
    yield IMAGE_TAG  # type: ignore[misc]
    subprocess.run(  # noqa: S603
        ["docker", "rmi", "-f", IMAGE_TAG],
        capture_output=True,  # noqa: S607
    )


def _seed_fernet_key(image: str, volume: str) -> None:
    """Drop a fake fernet.key into the volume so entrypoint's chmod fires.

    Entrypoint conditions on ``[ -f "$FERNET_PATH" ]`` and skips the chmod
    block if the file is absent. We seed it as ``root:root 0644`` so we
    can prove the chmod block actually changed it — not "test passed
    because the file was already in the target state."
    """
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/secrets",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            # 32 bytes = a plausible Fernet key length; the byte content is
            # irrelevant — we never use it as a real key, only check perms.
            "printf 'fake-fernet-key-32-bytes-aaaaaaaa' > /secrets/fernet.key && "
            "chmod 0644 /secrets/fernet.key",
        ],
        check=True,
        capture_output=True,
    )


def _stat(image: str, volume: str, path: str) -> str:
    """Return ``'owner:group mode'`` for a path in a volume."""
    r = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/inspect:ro",
            "--entrypoint",
            "stat",
            image,
            "-c",
            "%U:%G %a",
            f"/inspect/{path}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _read_as(image: str, volume: str, user: str, path: str) -> tuple[int, str]:
    """Try to read a file in a volume as ``user``. Return ``(rc, output)``.

    Uses ``docker run --user`` so the kernel evaluates the open() against
    the named uid's effective groups — the actual security boundary.
    """
    r = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume}:/inspect:ro",
            "--user",
            user,
            "--entrypoint",
            "cat",
            image,
            f"/inspect/{path}",
        ],
        capture_output=True,
        text=True,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def _run_entrypoint(image: str, volume: str, container: str, env: dict[str, str]) -> None:
    """Boot the image in detached mode, let entrypoint's chmod block run.

    The proxy may crash later (A1 doesn't ship the IPC verbs A3 needs),
    but the chmod runs synchronously BEFORE start.py exec's the proxy,
    so the volume is in its final state within ~3 seconds regardless.
    """
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        container,
        "-v",
        f"{volume}:/secrets",
        "--read-only",
        "--tmpfs",
        "/tmp:noexec,nosuid",
    ]
    for k, v in env.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.extend(_CAPS)
    cmd.append(image)
    subprocess.run(cmd, check=True, capture_output=True)  # noqa: S603
    # Give entrypoint's chmod block time to execute. It's synchronous
    # (4 lines of bash) so 3s is generous; we don't need the proxy to
    # finish booting — we only need the chown/chmod to have completed.
    time.sleep(3)


def _cleanup(container: str, volume: str) -> None:
    subprocess.run(  # noqa: S603
        ["docker", "rm", "-f", container],
        capture_output=True,  # noqa: S607
    )
    subprocess.run(  # noqa: S603
        ["docker", "volume", "rm", "-f", volume],
        capture_output=True,  # noqa: S607
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_off_keeps_legacy_root_worthless_0440(image: str) -> None:
    """WORTHLESS_FERNET_IPC_ONLY unset → fernet.key stays root:worthless 0440.

    This is what the existing docker-e2e relies on. Failure here means A1
    broke production behavior — must hard-fail.
    """
    vol = f"chmod-default-{uuid.uuid4().hex[:8]}"
    cnt = f"chmod-default-{uuid.uuid4().hex[:8]}"
    try:
        _seed_fernet_key(image, vol)
        _run_entrypoint(
            image,
            vol,
            cnt,
            env={
                # Point entrypoint at the seeded /secrets path. Default
                # is $HOME_DIR/fernet.key (=/data/fernet.key); we use
                # the migration env var so the chmod block targets the
                # file we control. Production uses /secrets too.
                "WORTHLESS_FERNET_KEY_PATH": "/secrets/fernet.key",
                "WORTHLESS_DEPLOY_MODE": "lan",
                "WORTHLESS_ALLOW_INSECURE": "true",
                "WORTHLESS_HOST": "0.0.0.0",
            },
        )
        owner_mode = _stat(image, vol, "fernet.key")
        assert owner_mode == "root:worthless 440", (
            "WOR-465 Phase A1 default-off regression: fernet.key must stay "
            f"root:worthless 0440 when WORTHLESS_FERNET_IPC_ONLY is unset; "
            f"got {owner_mode!r}. This breaks docker-e2e + bootstrap-validation."
        )
    finally:
        _cleanup(cnt, vol)


def test_flag_on_chowns_to_crypto_owner_and_locks_proxy_out(image: str) -> None:
    """WORTHLESS_FERNET_IPC_ONLY=1 → worthless-crypto:worthless-crypto 0400, proxy denied.

    This is the test that should have caught the chown root:worthless-crypto
    0400 bug. It exercises three claims at the kernel level:

    1. ownership flips to worthless-crypto:worthless-crypto
    2. mode is 0400 (owner-only, not group-readable)
    3. the worthless-crypto sidecar uid CAN read (owner bit), the
       worthless-proxy uid CANNOT (no group bit set)

    Static-text grep cannot make claim 3 — only a real kernel check can.
    """
    vol = f"chmod-on-{uuid.uuid4().hex[:8]}"
    cnt = f"chmod-on-{uuid.uuid4().hex[:8]}"
    try:
        _seed_fernet_key(image, vol)
        _run_entrypoint(
            image,
            vol,
            cnt,
            env={
                "WORTHLESS_FERNET_IPC_ONLY": "1",
                "WORTHLESS_FERNET_KEY_PATH": "/secrets/fernet.key",
                "WORTHLESS_DEPLOY_MODE": "lan",
                "WORTHLESS_ALLOW_INSECURE": "true",
                "WORTHLESS_HOST": "0.0.0.0",
            },
        )

        # Claim 1 + 2: ownership and mode
        owner_mode = _stat(image, vol, "fernet.key")
        assert owner_mode == "worthless-crypto:worthless-crypto 400", (
            "WOR-465 Phase A1: WORTHLESS_FERNET_IPC_ONLY=1 must flip "
            "fernet.key to worthless-crypto:worthless-crypto 0400 "
            f"(sidecar owns, owner-only). Got {owner_mode!r}. "
            "Common bug: chown root:worthless-crypto leaves owner=root, "
            "and 0400 then locks the sidecar out of its own key."
        )

        # Claim 3a: sidecar uid (worthless-crypto) CAN read via owner bit
        crypto_rc, crypto_out = _read_as(image, vol, "worthless-crypto", "fernet.key")
        assert crypto_rc == 0, (
            "WOR-465 Phase A1: worthless-crypto sidecar uid must be able "
            f"to read fernet.key (owner bit). Got rc={crypto_rc} "
            f"output={crypto_out!r}. If owner is root with mode 0400, the "
            "sidecar — the only legitimate reader — is locked out, and "
            "the flag becomes a denial-of-service."
        )

        # Claim 3b: proxy uid CANNOT read — kernel enforces the boundary
        proxy_rc, proxy_out = _read_as(image, vol, "worthless-proxy", "fernet.key")
        assert proxy_rc != 0, (
            "WOR-465 Phase A1: worthless-proxy uid MUST NOT be able to read "
            f"fernet.key when WORTHLESS_FERNET_IPC_ONLY=1. Got rc={proxy_rc}. "
            "This is the entire security claim of WOR-465 — if the proxy "
            "can still open() the key, the offline-key-theft gap is wide open."
        )
        assert "denied" in proxy_out.lower() or "eacces" in proxy_out.lower(), (
            "WOR-465 Phase A1: proxy read failure must be EACCES (kernel "
            f"denial), not some other error. Got: {proxy_out!r}"
        )
    finally:
        _cleanup(cnt, vol)
