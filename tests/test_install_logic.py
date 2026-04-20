"""Subprocess-level behavioral tests for install.sh (WOR-235).

These run the actual install.sh under sh(1) with a mocked PATH so we can
assert exit codes and stderr/stdout patterns without touching the network
or the user's real environment. Heavier integration coverage lives in
test_install_docker.py (marked 'docker').
"""

from __future__ import annotations

from pathlib import Path

from tests._install_helpers import run_install, write_stub


def test_windows_native_exits_20_with_link(tmp_path: Path) -> None:
    """Windows native (MINGW/CYGWIN) must die with exit 20 + helpful link (UX)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "uname", 'echo "MINGW64_NT-10.0"')

    result = run_install(bin_dir)

    assert result.returncode == 20, (
        f"expected exit 20 (unsupported platform), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "windows" in result.stderr.lower(), (
        "stderr must mention Windows so user understands the failure"
    )
    assert "worthless.sh" in result.stderr or "docs" in result.stderr.lower(), (
        "stderr must include a docs link, not a generic die message"
    )


def test_macos_below_11_exits_20(tmp_path: Path) -> None:
    """macOS <11 must die with exit 20 + version mention (OS reviewer)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "uname", "echo Darwin")
    write_stub(bin_dir, "sw_vers", 'echo "10.15.7"')

    result = run_install(bin_dir)

    assert result.returncode == 20, (
        f"expected exit 20 (unsupported platform) on macOS 10.15, got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert (
        "11" in result.stderr
        or "big sur" in result.stderr.lower()
        or "macos" in result.stderr.lower()
    ), "stderr must mention the macOS version requirement"


def test_pipx_conflict_warns_and_exits_30(tmp_path: Path) -> None:
    """Pre-existing pipx-installed worthless triggers exit 30 with uninstall hint."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "uname", "echo Darwin")
    write_stub(bin_dir, "sw_vers", 'echo "14.5"')
    # pipx list reports an existing worthless install
    write_stub(
        bin_dir,
        "pipx",
        """case "$1" in
  list) echo "package worthless 0.3.0, installed using Python 3.12.0" ;;
  *) echo "pipx stub: $@" ;;
esac
exit 0""",
    )

    result = run_install(bin_dir)

    assert result.returncode == 30, (
        f"expected exit 30 (pipx conflict) when pipx has worthless, got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert "pipx uninstall worthless" in result.stderr, (
        "stderr must include the exact 'pipx uninstall worthless' command"
    )


def test_curl_network_failure_exits_10(tmp_path: Path) -> None:
    """curl failing (network) must exit 10 (UX exit-code contract)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "uname", "echo Darwin")
    write_stub(bin_dir, "sw_vers", 'echo "14.5"')
    # curl always fails — simulates network outage
    write_stub(bin_dir, "curl", 'echo "curl: (6) Could not resolve host" >&2; exit 6')

    result = run_install(bin_dir)

    assert result.returncode == 10, (
        f"expected exit 10 (network failure) when curl fails, got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
