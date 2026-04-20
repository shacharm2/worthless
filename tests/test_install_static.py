"""Static-content checks on install.sh (WOR-235).

These are cheap regression guards: the rewritten install.sh must contain
specific safety / configuration markers agreed in the multi-agent design
review. Static checks complement the subprocess-level behavioral tests
in test_install_logic.py and the Docker integration tests.
"""

from __future__ import annotations

import re

import pytest

from tests._install_helpers import INSTALL_SH


@pytest.fixture(scope="module")
def install_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_set_eu_present(install_text: str) -> None:
    """Fail-fast on undefined vars and command failures (security review P0)."""
    assert re.search(r"^\s*set\s+-eu\b", install_text, re.MULTILINE), (
        "install.sh must use 'set -eu' to prevent silent-empty-pipe class bugs"
    )


def test_uv_version_pinned(install_text: str) -> None:
    """uv version pinned (brutus + security P0)."""
    assert re.search(r"\bUV_VERSION\s*=\s*['\"]?\d+\.\d+", install_text), (
        "install.sh must pin UV_VERSION to a specific release"
    )


def test_worthless_version_pinned(install_text: str) -> None:
    """worthless install pinned to a specific version (security P0)."""
    assert re.search(r"worthless==\d+\.\d+\.\d+", install_text), (
        "install.sh must invoke 'uv tool install worthless==<version>' (no floating tag)"
    )


def test_sha256_verification_referenced(install_text: str) -> None:
    """Astral installer SHA256 verification (security P0)."""
    assert re.search(r"sha256", install_text, re.IGNORECASE), (
        "install.sh must reference SHA256 verification of the Astral installer"
    )


def test_distinct_exit_codes(install_text: str) -> None:
    """Distinct exit codes 10/20/30 for network/platform/pipx-conflict (UX)."""
    for code, kind in [(10, "network"), (20, "unsupported platform"), (30, "pipx conflict")]:
        assert f"exit {code}" in install_text, (
            f"install.sh must use 'exit {code}' for {kind} failures"
        )


def test_uv_python_preference_only_managed(install_text: str) -> None:
    """UV_PYTHON_PREFERENCE=only-managed (OS reviewer)."""
    assert "UV_PYTHON_PREFERENCE" in install_text, (
        "install.sh must set UV_PYTHON_PREFERENCE for reproducibility"
    )
    assert "only-managed" in install_text, (
        "UV_PYTHON_PREFERENCE must be 'only-managed' (not 'managed') for fresh-box reliability"
    )


def test_per_shell_activation_messages(install_text: str) -> None:
    """Per-shell PATH activation one-liners for bash, zsh, fish (UX + OS)."""
    for shell in ("bash", "zsh", "fish"):
        assert shell in install_text, (
            f"install.sh must include {shell}-specific activation guidance"
        )


def test_wsl_allowed_not_rejected(install_text: str) -> None:
    """WSL2 must be allowed; only /mnt/c gets a warning (OS + UX)."""
    # Old behavior: rejected WSL outright. New: detect, allow, warn on /mnt/c.
    if "microsoft" in install_text.lower() or "wsl" in install_text.lower():
        # If WSL is detected, it must NOT call die/exit-with-non-zero in that branch.
        wsl_section = re.search(r"(?is)(microsoft|wsl).{0,400}", install_text)
        assert wsl_section, "Expected WSL detection block"
        snippet = wsl_section.group(0).lower()
        assert "die " not in snippet and "exit 1" not in snippet, (
            "WSL2 detection must NOT reject — only /mnt/c gets a warning"
        )


def test_pipx_conflict_detection(install_text: str) -> None:
    """Detect pipx-installed worthless and warn (brutus + security)."""
    assert "pipx" in install_text, "install.sh must detect pre-existing pipx-installed worthless"


def test_macos_min_version_check(install_text: str) -> None:
    """Pre-flight check for macOS >=11 (OS reviewer)."""
    assert "sw_vers" in install_text, "install.sh must use sw_vers to enforce macOS >=11 minimum"


def test_curl_fail_retry(install_text: str) -> None:
    """curl uses --fail and --retry to avoid silent-empty-pipe (OS + security)."""
    assert "--fail" in install_text, "curl must use --fail to error on HTTP 4xx/5xx"
    assert "--retry" in install_text, "curl must use --retry for transient failures"


def test_idempotent_upgrade_path(install_text: str) -> None:
    """Re-runs route to 'uv tool upgrade' instead of failing on duplicate install."""
    assert "uv tool upgrade" in install_text, (
        "install.sh must support idempotent re-runs via 'uv tool upgrade'"
    )


def test_doctor_breadcrumb_printed(install_text: str) -> None:
    """Last-line breadcrumb to 'worthless doctor' (UX, free real estate)."""
    assert "worthless doctor" in install_text, (
        "install.sh must end with 'Run worthless doctor if anything looks off' breadcrumb"
    )


def test_proxy_remediation_hints(install_text: str) -> None:
    """Failure messages mention proxy / mirror env vars (OS + UX)."""
    # At least one of the corp-network env vars should be referenced for self-help.
    assert any(
        marker in install_text
        for marker in ("HTTPS_PROXY", "UV_PYTHON_INSTALL_MIRROR", "SSL_CERT_FILE")
    ), "install.sh must surface proxy/mirror remediation hints on network failures"


def test_smoke_test_uses_uv_run_version(install_text: str) -> None:
    """Smoke test uses 'uv run worthless --version' (UX), not 'worthless lock'."""
    assert "uv run worthless" in install_text or "worthless --version" in install_text, (
        "install.sh must smoke-test via 'uv run worthless --version' (PATH-independent)"
    )
    assert "worthless lock" not in install_text, (
        "Do NOT smoke-test with 'worthless lock' — too stateful for an installer"
    )
