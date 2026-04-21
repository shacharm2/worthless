"""Static-content checks on install.sh.

Regression guards for safety/config markers. Complement subprocess tests
in test_install_logic.py and Docker tests in test_install_docker.py.
"""

from __future__ import annotations

import re

import pytest

from tests._install_helpers import INSTALL_SH


@pytest.fixture(scope="module")
def install_text() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def test_set_eu_present(install_text: str) -> None:
    assert re.search(r"^\s*set\s+-eu\b", install_text, re.MULTILINE), (
        "install.sh must use 'set -eu' to prevent silent-empty-pipe class bugs"
    )


def test_uv_version_pinned(install_text: str) -> None:
    assert re.search(r"\bUV_VERSION\s*=\s*['\"]?\d+\.\d+", install_text), (
        "install.sh must pin UV_VERSION to a specific release"
    )


def test_worthless_version_pinned(install_text: str) -> None:
    assert re.search(r'^\s*WORTHLESS_VERSION\s*=\s*"\d+\.\d+\.\d+"', install_text, re.MULTILINE), (
        "install.sh must pin WORTHLESS_VERSION to a specific x.y.z release (no floating tag)"
    )
    assert re.search(
        r"worthless==\$\{?WORTHLESS_VERSION\}?|worthless==\d+\.\d+\.\d+", install_text
    ), "install.sh must install 'worthless==<version>' using the pinned variable"


def test_sha256_verification_referenced(install_text: str) -> None:
    assert re.search(r"sha256", install_text, re.IGNORECASE), (
        "install.sh must reference SHA256 verification of the Astral installer"
    )


def test_distinct_exit_codes(install_text: str) -> None:
    """Named constants for network/platform/pipx-conflict/internal (UX contract)."""
    for name, code in [
        ("EXIT_NETWORK", 10),
        ("EXIT_PLATFORM", 20),
        ("EXIT_PIPX_CONFLICT", 30),
        ("EXIT_INTERNAL", 40),
    ]:
        assert re.search(rf"^\s*{name}\s*=\s*{code}\b", install_text, re.MULTILINE), (
            f"install.sh must declare {name}={code} as a named exit-code constant"
        )


def test_uv_python_preference_only_managed(install_text: str) -> None:
    assert "UV_PYTHON_PREFERENCE" in install_text, (
        "install.sh must set UV_PYTHON_PREFERENCE for reproducibility"
    )
    assert "only-managed" in install_text, (
        "UV_PYTHON_PREFERENCE must be 'only-managed' (not 'managed') for fresh-box reliability"
    )


def test_per_shell_activation_messages(install_text: str) -> None:
    for shell in ("bash", "zsh", "fish"):
        assert shell in install_text, (
            f"install.sh must include {shell}-specific activation guidance"
        )


def _extract_shell_function(text: str, name: str) -> str:
    """Return the body of a POSIX-sh function definition `name() { ... }`."""
    match = re.search(rf"^{re.escape(name)}\s*\(\)\s*\{{(.*?)^\}}", text, re.DOTALL | re.MULTILINE)
    assert match, f"Expected function {name}() to be defined in install.sh"
    return match.group(1)


def test_wsl_allowed_not_rejected(install_text: str) -> None:
    """WSL2 must be allowed; only /mnt/c gets a warning (OS + UX)."""
    body = _extract_shell_function(install_text, "detect_linux_subenv")
    lower = body.lower()
    assert "microsoft" in lower or "wsl" in lower, (
        "detect_linux_subenv() must detect WSL via /proc/version"
    )
    assert re.search(r"\bdie\b", body) is None, (
        "WSL2 detection must NOT call die() — only /mnt/c gets a warning"
    )
    assert "exit " not in body, "WSL2 detection must NOT exit non-zero — WSL2 is supported"


def test_pipx_conflict_detection(install_text: str) -> None:
    assert "pipx" in install_text, "install.sh must detect pre-existing pipx-installed worthless"


def test_macos_min_version_check(install_text: str) -> None:
    assert "sw_vers" in install_text, "install.sh must use sw_vers to enforce macOS >=11 minimum"


def test_curl_fail_retry(install_text: str) -> None:
    assert "--fail" in install_text, "curl must use --fail to error on HTTP 4xx/5xx"
    assert "--retry" in install_text, "curl must use --retry for transient failures"


def test_idempotent_upgrade_path(install_text: str) -> None:
    assert "uv tool upgrade" in install_text, (
        "install.sh must support idempotent re-runs via 'uv tool upgrade'"
    )


def test_doctor_breadcrumb_printed(install_text: str) -> None:
    assert "worthless doctor" in install_text, (
        "install.sh must end with 'Run worthless doctor if anything looks off' breadcrumb"
    )


def test_proxy_remediation_hints(install_text: str) -> None:
    assert any(
        marker in install_text
        for marker in ("HTTPS_PROXY", "UV_PYTHON_INSTALL_MIRROR", "SSL_CERT_FILE")
    ), "install.sh must surface proxy/mirror remediation hints on network failures"


def test_smoke_test_uses_uv_run_version(install_text: str) -> None:
    """Smoke test stays stateless — 'worthless lock' would bootstrap state we don't own."""
    assert "uv run worthless" in install_text or "worthless --version" in install_text, (
        "install.sh must smoke-test via 'uv run worthless --version' (PATH-independent)"
    )
    assert "worthless lock" not in install_text, (
        "Do NOT smoke-test with 'worthless lock' — too stateful for an installer"
    )
