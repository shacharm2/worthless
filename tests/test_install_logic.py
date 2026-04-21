"""Subprocess-level behavioral tests for install.sh.

These run the actual install.sh under sh(1) with a mocked PATH so we can
assert exit codes and stderr/stdout patterns without touching the network
or the user's real environment. Heavier integration coverage lives in
test_install_docker.py (marked 'docker').
"""

from __future__ import annotations

from pathlib import Path

from tests._install_helpers import (
    EXIT_NETWORK,
    EXIT_PIPX_CONFLICT,
    EXIT_PLATFORM,
    run_install,
    write_happy_path_stubs,
    write_stub,
)


def test_windows_native_exits_20_with_link(tmp_path: Path) -> None:
    """Windows native (MINGW/CYGWIN) must die with exit 20 + helpful link (UX)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "uname", 'echo "MINGW64_NT-10.0"')

    result = run_install(bin_dir)

    assert result.returncode == EXIT_PLATFORM, (
        f"expected exit {EXIT_PLATFORM} (unsupported platform), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "windows" in result.stderr.lower(), (
        "stderr must mention Windows so user understands the failure"
    )
    assert "worthless.sh" in result.stderr, (
        "stderr must include a worthless.sh docs link, not a generic die message"
    )


def test_macos_below_11_exits_20(tmp_path: Path) -> None:
    """macOS <11 must die with exit 20 + version mention (OS reviewer)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "uname", "echo Darwin")
    write_stub(bin_dir, "sw_vers", 'echo "10.15.7"')

    result = run_install(bin_dir)

    assert result.returncode == EXIT_PLATFORM, (
        f"expected exit {EXIT_PLATFORM} (unsupported platform) on macOS 10.15, "
        f"got {result.returncode}\nstderr: {result.stderr}"
    )
    # Must pin the version requirement specifically, not just say "macOS".
    assert "11" in result.stderr, "stderr must cite the macOS 11 minimum version"
    assert "big sur" in result.stderr.lower() or "macos" in result.stderr.lower(), (
        "stderr must name the OS/release so the user knows what to upgrade"
    )


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

    assert result.returncode == EXIT_PIPX_CONFLICT, (
        f"expected exit {EXIT_PIPX_CONFLICT} (pipx conflict) when pipx has worthless, "
        f"got {result.returncode}\nstderr: {result.stderr}"
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

    assert result.returncode == EXIT_NETWORK, (
        f"expected exit {EXIT_NETWORK} (network failure) when curl fails, got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )


def test_success_with_persistent_rc_shows_clean_done_message(tmp_path: Path) -> None:
    """Happy path + rc references ~/.local/bin → "on your PATH", no extra hints.

    This is the "already set up" user (e.g. upgrading). They don't need noise
    about making PATH permanent — their rc file already did it.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_happy_path_stubs(bin_dir)
    # Simulate a zsh user whose .zshrc already adds ~/.local/bin to PATH.
    (tmp_path / ".zshrc").write_text('export PATH="$HOME/.local/bin:$PATH"\n')

    result = run_install(bin_dir)

    assert result.returncode == 0, (
        f"happy path must exit 0, got {result.returncode}\nstderr: {result.stderr}"
    )
    assert "is on your PATH" in result.stdout, (
        f"stdout must confirm worthless is on PATH.\nstdout: {result.stdout}"
    )
    assert "works in this shell" not in result.stdout, (
        "must not nag persistent users about 'this shell only' — they're already set up"
    )
    assert "Heads up" not in result.stdout, (
        "must not warn persistent users about new terminals — their rc handles it"
    )


def test_success_without_persistent_rc_warns_and_shows_persistence_hint(
    tmp_path: Path,
) -> None:
    """Happy path + no rc persistence → warn + persistence hint (no activation).

    This is the fresh-macOS papercut we're fixing. install.sh exported
    ~/.local/bin for its own subprocesses, so `command -v worthless` succeeds,
    but a new terminal won't. We must flag that loudly and give the exact
    one-liner to fix it.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_happy_path_stubs(bin_dir)
    # No rc files — simulates a fresh user account.

    result = run_install(bin_dir)

    assert result.returncode == 0, (
        f"happy path must exit 0, got {result.returncode}\nstderr: {result.stderr}"
    )
    assert "works in this shell" in result.stdout, (
        f"must tell the user PATH is live here but not persistent.\nstdout: {result.stdout}"
    )
    # warn() routes to stderr, so the "Heads up" banner lives there.
    assert "Heads up" in result.stderr, (
        f"must warn explicitly that a new terminal won't find worthless.\nstderr: {result.stderr}"
    )
    assert "Make permanent" in result.stdout, (
        "must print the make-permanent one-liner so the user can fix it"
    )
    assert "Activate in this shell" not in result.stdout, (
        "must NOT print 'Activate in this shell' — PATH is already live here; "
        "that hint would confuse users into thinking worthless doesn't work yet"
    )
