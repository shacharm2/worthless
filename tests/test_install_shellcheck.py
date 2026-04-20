"""shellcheck static analysis for install.sh (WOR-235).

Skipped if shellcheck is not installed locally; required in CI.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests._install_helpers import INSTALL_SH


@pytest.mark.skipif(
    shutil.which("shellcheck") is None,
    reason="shellcheck not installed; install via 'brew install shellcheck' or apt",
)
def test_install_sh_passes_shellcheck() -> None:
    """install.sh must pass shellcheck with no errors or warnings."""
    result = subprocess.run(  # noqa: S603
        ["shellcheck", "--shell=sh", "--severity=warning", str(INSTALL_SH)],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"shellcheck reported issues:\n{result.stdout}\n{result.stderr}"
