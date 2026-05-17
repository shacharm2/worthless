"""Shared helpers for install.sh tests."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_FIXTURES = REPO_ROOT / "tests" / "install_fixtures"

EXIT_NETWORK = 10
EXIT_PLATFORM = 20
EXIT_PIPX_CONFLICT = 30
EXIT_INTERNAL = 40


def write_stub(bin_dir: Path, name: str, body: str) -> Path:
    """Write an executable POSIX sh stub at ``bin_dir/name``."""
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# Pinned uv version must match install.sh UV_VERSION. Kept in sync manually —
# a mismatch here breaks happy-path tests immediately, which is the signal.
_UV_VERSION = "0.11.7"


def write_happy_path_stubs(bin_dir: Path, *, with_worthless: bool = True) -> None:
    """Stub every binary install.sh invokes on the Darwin success path.

    This lets tests exercise the *final* messaging branches (on-your-PATH vs
    works-in-this-shell) without touching the network or really installing
    anything.

    ``with_worthless=False`` simulates the case where `uv tool install` ran
    but ~/.local/bin isn't on the test's synthetic PATH — we still hit the
    "not yet on your PATH" branch.
    """
    write_stub(bin_dir, "uname", "echo Darwin")
    write_stub(bin_dir, "sw_vers", 'echo "14.5"')
    write_stub(
        bin_dir,
        "uv",
        f"""case "$1" in
  --version) echo "uv {_UV_VERSION}" ;;
  tool) shift; case "$1" in
    install|upgrade|list) echo "ok" ;;
    *) echo "uv tool: unhandled: $*" >&2; exit 1 ;;
  esac ;;
  run) echo "worthless 0.3.0" ;;
  *) echo "uv: unhandled: $*" >&2; exit 1 ;;
esac""",
    )
    if with_worthless:
        write_stub(bin_dir, "worthless", 'echo "worthless 0.3.0"')


def run_install(
    bin_dir: Path,
    env_extra: dict[str, str] | None = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """Run install.sh with ``bin_dir`` first on PATH; return CompletedProcess."""
    base_path = "/usr/bin:/bin:/usr/sbin:/sbin"
    env = {
        "PATH": f"{bin_dir}:{base_path}",
        "HOME": str(bin_dir.parent),
        "SHELL": "/bin/zsh",
        # WOR-463: prevent any subprocess `worthless` invocation from
        # writing fernet-key-* entries to the host's real keychain.
        # install.sh's smoke_test stubs `worthless` today, but this stays
        # defensive against future install.sh paths that exec the binary.
        "WORTHLESS_KEYRING_BACKEND": "null",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(  # noqa: S603
        ["sh", str(INSTALL_SH)],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


__all__ = [
    "EXIT_INTERNAL",
    "EXIT_NETWORK",
    "EXIT_PIPX_CONFLICT",
    "EXIT_PLATFORM",
    "INSTALL_FIXTURES",
    "INSTALL_SH",
    "REPO_ROOT",
    "run_install",
    "write_happy_path_stubs",
    "write_stub",
]
