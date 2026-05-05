"""Static-content checks on install.sh.

Regression guards for safety/config markers. Complement subprocess tests
in test_install_logic.py and Docker tests in test_install_docker.py.
"""

from __future__ import annotations

import re

import pytest

from tests._install_helpers import INSTALL_FIXTURES, INSTALL_SH


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


# --- WOR-319: supply-chain pinning of base images + Astral installer SHA -----


def test_dockerfiles_pin_base_image_digests() -> None:
    """Every FROM line in install fixtures must pin to @sha256:<digest>.

    Floating tags (`ubuntu:24.04`, `python:3.13-slim-bookworm`) let a
    compromised upstream ship malware through our install matrix. Pinning
    by sha256 digest makes the supply chain reproducible — the digest is
    the contract, the tag is just a label.
    """
    dockerfiles = sorted(INSTALL_FIXTURES.glob("Dockerfile.*"))
    assert dockerfiles, "expected Dockerfile fixtures under tests/install_fixtures/"

    digest_re = re.compile(r"^FROM\s+\S+@sha256:[0-9a-f]{64}\b", re.MULTILINE)
    from_re = re.compile(r"^FROM\s+\S+", re.MULTILINE)

    unpinned: list[str] = []
    for f in dockerfiles:
        text = f.read_text(encoding="utf-8")
        from_lines = from_re.findall(text)
        digest_lines = digest_re.findall(text)
        if len(from_lines) != len(digest_lines):
            unpinned.append(f"{f.name}: {from_lines}")

    assert not unpinned, (
        "install fixtures must pin base images by @sha256 digest — "
        "floating tags allow upstream tampering to enter the matrix.\n"
        "Unpinned FROM lines:\n  " + "\n  ".join(unpinned)
    )


def test_ubuntu_with_uv_pins_astral_installer() -> None:
    """Dockerfile.ubuntu-with-uv must verify the Astral installer SHA.

    A raw `curl … | sh` pipeline executes whatever Astral serves at fetch
    time. The fixture must instead: (1) download to a file, (2) verify
    against the same SHA constant install.sh enforces, (3) only then run.
    Without this, a compromised CDN slips arbitrary code into our test
    matrix even though install.sh itself is hardened.
    """
    dockerfile = INSTALL_FIXTURES / "Dockerfile.ubuntu-with-uv"
    text = dockerfile.read_text(encoding="utf-8")

    # Negative: no `curl … astral.sh … | sh` pipe (raw remote-exec).
    assert not re.search(
        r"curl[^|]*astral\.sh[^|]*\|\s*sh\b",
        text,
    ), (
        "Dockerfile.ubuntu-with-uv must NOT run `curl … astral.sh … | sh` — "
        "fetch to a file and verify SHA256 before executing."
    )

    # Positive: download → sha256 verification → execute.
    assert re.search(r"sha256sum|shasum\s+-a\s+256", text), (
        "Dockerfile.ubuntu-with-uv must verify the downloaded Astral "
        "installer with sha256sum (or shasum -a 256) before running it."
    )

    # Positive: SHA constant is sourced from install.sh, not a copy.
    # We `awk` it out of the on-disk install.sh so a UV_VERSION bump in
    # install.sh propagates into the fixture without manual edits.
    assert re.search(r"ASTRAL_INSTALLER_SHA256", text), (
        "Dockerfile.ubuntu-with-uv must reference ASTRAL_INSTALLER_SHA256 "
        "(extracted from install.sh) so the SHA stays in lockstep."
    )


# --- WOR-320: BuildKit cache mount for uv downloads --------------------------


def _run_blocks(dockerfile_text: str) -> list[str]:
    """Return each RUN command in a Dockerfile as a single string.

    A RUN command spans the `RUN` line plus any `\\`-continuation lines.
    It ends when a line not ending in `\\` is reached. CMD / COPY lines
    that follow the RUN do NOT belong to it.
    """
    blocks: list[str] = []
    lines = dockerfile_text.splitlines()
    i = 0
    while i < len(lines):
        if re.match(r"^RUN\b", lines[i]):
            buf: list[str] = []
            while i < len(lines):
                stripped = lines[i].rstrip()
                continuation = stripped.endswith("\\")
                buf.append(stripped[:-1].rstrip() if continuation else stripped)
                i += 1
                if not continuation:
                    break
            blocks.append(" ".join(buf))
        else:
            i += 1
    return blocks


def _run_clauses(dockerfile_text: str) -> list[tuple[str, str]]:
    """Return (block, clause) pairs for each command inside RUN blocks.

    A multi-clause RUN (chained with `&&` or `;`) splits into one clause
    per command so `chmod +x install.sh` and `sh /work/install.sh` are
    classified independently. The full block is returned alongside so
    callers can locate the cache-mount directive (which lives on the
    RUN keyword, not inside individual clauses).
    """
    pairs: list[tuple[str, str]] = []
    for block in _run_blocks(dockerfile_text):
        body = block[3:]  # drop leading "RUN"
        for clause in re.split(r"&&|;", body):
            clause = clause.strip()
            if clause:
                pairs.append((block, clause))
    return pairs


def test_dockerfiles_use_uv_cache_mount() -> None:
    """Build-time RUN that runs uv must use BuildKit cache mount.

    /root/.cache/uv holds Astral installer downloads + Python bootstrap
    tarballs (PBS) + wheel cache. Without the cache mount, every matrix
    run re-fetches the same MB-sized payloads from astral.sh, slowing CI
    and exposing us to upstream rate limits.

    Scope: any Dockerfile fixture with a build-time RUN that executes
    install.sh, the Astral installer, or a `uv …` command. CMD-only
    fixtures (where install.sh runs at container start) are skipped —
    BuildKit cache mounts apply at build time only.
    """
    dockerfiles = sorted(INSTALL_FIXTURES.glob("Dockerfile.*"))
    cache_mount_re = re.compile(
        r"--mount=type=cache,target=/root/\.cache/uv\b",
    )
    syntax_directive_re = re.compile(r"^#\s*syntax=docker/dockerfile:1\.\d", re.MULTILINE)

    # A RUN clause is "uv-running" if its FIRST verb executes install.sh,
    # downloads the Astral installer, runs the installer, or invokes a
    # `uv` subcommand. Anchoring at the start of a clause excludes
    # chmod-on-install.sh (sets bits) and `! command -v uv` (sentinel).
    uv_run_re = re.compile(
        r"^(?:sh|bash)\s+\S*install\.sh"  # `sh /work/install.sh`
        r"|^(?:sh|bash)\s+\S*uv-installer"  # `sh /tmp/uv-installer.sh`
        r"|astral\.sh/uv/"  # downloading from astral
        r"|^/\S*\.local/bin/uv\s"  # running the installed binary
        r"|^uv\s+(?:tool|install|sync|run|cache|venv|python|self)\b"
    )

    missing: list[str] = []
    for f in dockerfiles:
        text = f.read_text(encoding="utf-8")
        uv_blocks = sorted(
            {block for block, clause in _run_clauses(text) if uv_run_re.search(clause)}
        )
        if not uv_blocks:
            continue  # CMD-only fixture — cache mount doesn't apply
        if not any(cache_mount_re.search(b) for b in uv_blocks):
            missing.append(f"{f.name}: uv-running RUN without cache mount")
            continue
        if not syntax_directive_re.search(text):
            missing.append(
                f"{f.name}: cache mount used but `# syntax=docker/dockerfile:1.x` "
                "directive missing — RUN --mount requires the directive"
            )

    assert not missing, (
        "Build-time RUN steps invoking uv must mount the BuildKit cache:\n  " + "\n  ".join(missing)
    )


# --- WOR-318: non-root user fixture ------------------------------------------


def test_nonroot_fixture_exists() -> None:
    """A hardened non-root container must be in the install matrix.

    install.sh writes to ~/.local/bin and ~/.cache/uv — both rooted at
    $HOME — so it should already work for non-root users. This fixture
    proves it: a `worthless` UID under `useradd` (no sudo, no root)
    completes install and ends up with the binary on PATH.

    The actual matrix wiring lives in tests/test_install_docker.py
    INSTALL_MATRIX. This static guard pins the file's existence and
    minimal shape so the matrix entry can never reference a missing
    fixture.
    """
    dockerfile = INSTALL_FIXTURES / "Dockerfile.ubuntu-nonroot"
    assert dockerfile.is_file(), (
        f"missing fixture: {dockerfile.name} — non-root install must be in the matrix"
    )

    text = dockerfile.read_text(encoding="utf-8")
    assert re.search(r"^RUN\s+useradd\b", text, re.MULTILINE), (
        f"{dockerfile.name} must `useradd` a non-root user before `USER` switch"
    )
    assert re.search(r"^USER\s+worthless\b", text, re.MULTILINE), (
        f"{dockerfile.name} must drop privileges via `USER worthless`"
    )


def test_worthless_version_resolution(install_text: str) -> None:
    """Resolve-latest pattern (Ollama / Bun / Deno style):

    install.sh does NOT hardcode a `WORTHLESS_VERSION="x.y.z"` literal —
    uv resolves the latest from PyPI at install time. The user-pin escape
    hatch is `WORTHLESS_VERSION=x.y.z curl … | sh`, validated against a
    PEP-440-ish charset before reaching `uv tool install`. See README
    "Versioning" section for the rationale.
    """
    # Negative: no hardcoded x.y.z constant.
    assert not re.search(
        r'^\s*WORTHLESS_VERSION\s*=\s*"\d+\.\d+\.\d+"', install_text, re.MULTILINE
    ), (
        "install.sh must NOT hardcode WORTHLESS_VERSION — version resolves "
        "from PyPI at install time. Use `${WORTHLESS_VERSION:+==…}` instead."
    )
    # Positive: env-var-pin escape hatch via POSIX `${VAR:+…}` expansion.
    assert re.search(
        r"worthless\$\{WORTHLESS_VERSION:\+==\$\{?WORTHLESS_VERSION\}?\}",
        install_text,
    ), (
        "install.sh must use the env-var-pin escape hatch: "
        "`worthless${WORTHLESS_VERSION:+==${WORTHLESS_VERSION}}`"
    )
    # Positive: input validator on user-supplied env var (defends against
    # shell metachars and arg-confusion before `uv tool install` is invoked).
    assert re.search(r"\[\!0-9A-Za-z\.\+\!-\]", install_text), (
        "install.sh must validate WORTHLESS_VERSION against a PEP-440-ish "
        "charset (POSIX case pattern with bracket negation)."
    )


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
    # Scope the "no stateful smoke" invariant to the smoke_test() function
    # body. The user-facing post-install banner legitimately mentions
    # `worthless lock` as a "Try it" hint — that is not a smoke-test action,
    # and a whole-script grep falsely flagged it. Pin the structural
    # contract: the function exists AND its body contains no `worthless lock`.
    smoke_match = re.search(
        r"^smoke_test\(\)\s*\{(.+?)^\}",
        install_text,
        re.MULTILINE | re.DOTALL,
    )
    assert smoke_match is not None, (
        "could not locate `smoke_test()` function in install.sh — "
        "test needs updating if the function was renamed"
    )
    smoke_body = smoke_match.group(1)
    assert "worthless lock" not in smoke_body, (
        "Do NOT smoke-test with 'worthless lock' — too stateful for an installer"
    )
