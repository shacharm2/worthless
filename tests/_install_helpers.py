"""Shared helpers for install.sh tests (WOR-235)."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_FIXTURES = REPO_ROOT / "tests" / "install_fixtures"


def write_stub(bin_dir: Path, name: str, body: str) -> Path:
    """Write an executable POSIX sh stub at ``bin_dir/name``."""
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


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


__all__ = ["INSTALL_SH", "INSTALL_FIXTURES", "REPO_ROOT", "run_install", "write_stub"]
