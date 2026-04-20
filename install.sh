#!/bin/sh
# Worthless installer — https://worthless.sh
# Usage: curl -sSL https://worthless.sh | sh
#
# Exit codes (UX contract):
#   0   success
#   10  network failure (curl/Astral CDN/PyPI unreachable)
#   20  unsupported platform (Windows native, macOS <11, glibc <2.17)
#   30  conflicting pipx-installed worthless detected
#   40  unexpected internal failure (uv install crash, smoke-test failed)

set -eu

EXIT_NETWORK=10
EXIT_PLATFORM=20
EXIT_PIPX_CONFLICT=30
EXIT_INTERNAL=40

UV_VERSION="0.11.7"
WORTHLESS_VERSION="0.3.0"

# SHA256 of https://astral.sh/uv/${UV_VERSION}/install.sh — bump with UV_VERSION.
ASTRAL_INSTALLER_SHA256="efed99618cb5c31e4e36a700ab7c3698e83c0ae0f3c336714043d0f932c8d32c"

ASTRAL_INSTALLER_URL="https://astral.sh/uv/${UV_VERSION}/install.sh"
DOCS_URL="https://docs.worthless.sh"
WINDOWS_DOCS_URL="https://docs.worthless.sh/install/windows"

# Force uv to use its own managed Python for fresh-box reproducibility.
export UV_PYTHON_PREFERENCE="${UV_PYTHON_PREFERENCE:-only-managed}"

# --- Output helpers ----------------------------------------------------------

setup_colors() {
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
        RED='\033[0;31m'
        GREEN='\033[0;32m'
        YELLOW='\033[0;33m'
        BOLD='\033[1m'
        RESET='\033[0m'
    else
        RED='' GREEN='' YELLOW='' BOLD='' RESET=''
    fi
}

info()  { printf "${BOLD}%s${RESET}\n" "$1"; }
ok()    { printf "${GREEN}%s${RESET}\n" "$1"; }
warn()  { printf "${YELLOW}%s${RESET}\n" "$1" >&2; }
err()   { printf "${RED}error: %s${RESET}\n" "$1" >&2; }

# die <exit-code> <message> [<hint-line>...]
die() {
    code="$1"; shift
    err "$1"; shift
    while [ "$#" -gt 0 ]; do
        printf "       %s\n" "$1" >&2
        shift
    done
    exit "$code"
}

proxy_hints() {
    printf "       Behind a proxy or corporate network? Try:\n" >&2
    printf "         export HTTPS_PROXY=https://your-proxy:port\n" >&2
    printf "         export UV_PYTHON_INSTALL_MIRROR=https://your-mirror/python-build-standalone\n" >&2
    printf "         export SSL_CERT_FILE=/path/to/corp-bundle.pem\n" >&2
}

# --- Platform detection ------------------------------------------------------

detect_os() {
    uname_s="$(uname -s 2>/dev/null || echo unknown)"
    case "$uname_s" in
        Darwin)
            OS="macos"
            check_macos_version
            ;;
        Linux)
            OS="linux"
            detect_linux_subenv
            ;;
        CYGWIN*|MINGW*|MSYS*)
            die "$EXIT_PLATFORM" "Windows native shells are not supported." \
                "Run inside a Linux subsystem instead:" \
                "  ${WINDOWS_DOCS_URL} (or see worthless.sh docs) for the full guide."
            ;;
        *)
            die "$EXIT_PLATFORM" "Unsupported OS: ${uname_s}" \
                "macOS >=11 and Linux (glibc >=2.17) are supported." \
                "  Docs: ${DOCS_URL}/install"
            ;;
    esac
}

check_macos_version() {
    # uv's python-build-standalone needs macOS 11 (Big Sur) minimum.
    if ! command -v sw_vers >/dev/null 2>&1; then
        warn "sw_vers not found; skipping macOS version check"
        return 0
    fi
    macos_ver="$(sw_vers -productVersion 2>/dev/null || echo "")"
    macos_major="$(echo "$macos_ver" | cut -d. -f1)"
    case "$macos_major" in
        ''|*[!0-9]*)
            die "$EXIT_PLATFORM" "Could not parse macOS version (got '${macos_ver}')." \
                "Big Sur (11) or newer is required." \
                "  Docs: ${DOCS_URL}/install"
            ;;
    esac
    if [ "$macos_major" -lt 11 ]; then
        die "$EXIT_PLATFORM" "macOS ${macos_ver} is too old — Big Sur (11) or newer required." \
            "uv's bundled Python (python-build-standalone) needs macOS >=11." \
            "  Upgrade macOS, or install via Homebrew: brew install pipx && pipx install worthless"
    fi
}

detect_linux_subenv() {
    # WSL2 is a fully supported Linux environment. Allow it; only warn when the
    # user is running from a Windows-mounted path, where Python tooling is slow.
    if [ -f /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null; then
        IS_WSL=1
        case "$(pwd)" in
            /mnt/*)
                warn "WSL detected, running from a Windows-mounted path (/mnt/...)."
                warn "Install will succeed but uv operations from /mnt/c are slow."
                warn "For best performance, install from your Linux home (~)."
                ;;
        esac
    fi
}

# --- Conflict detection ------------------------------------------------------

check_pipx_conflict() {
    # Stop early: a pipx shim on PATH would mask the uv-installed binary.
    if command -v pipx >/dev/null 2>&1; then
        if pipx list 2>/dev/null | grep -qi "package worthless "; then
            die "$EXIT_PIPX_CONFLICT" "Detected a pipx-installed worthless." \
                "uv and pipx both manage tool isolation; running both is confusing." \
                "Remove the pipx version, then re-run this installer:" \
                "  pipx uninstall worthless"
        fi
    fi
}

# --- uv install --------------------------------------------------------------

ensure_uv() {
    # Skip Astral installer entirely if uv is already at the pinned version.
    if command -v uv >/dev/null 2>&1; then
        existing_ver="$(uv --version 2>/dev/null | awk '{print $2}')"
        if [ "$existing_ver" = "$UV_VERSION" ]; then
            ok "  uv ${UV_VERSION} already installed"
            return 0
        fi
        info "  uv ${existing_ver} found; bootstrapping pinned uv ${UV_VERSION}"
    else
        info "  Installing uv ${UV_VERSION}..."
    fi

    tmpdir="$(mktemp -d 2>/dev/null || mktemp -d -t worthless-uv-XXXXXX)"
    trap 'rm -rf "$tmpdir"' EXIT INT TERM
    installer="$tmpdir/uv-installer.sh"

    if ! curl --fail --silent --show-error --location \
              --retry 3 --retry-delay 2 --max-time 30 \
              --output "$installer" \
              "$ASTRAL_INSTALLER_URL"; then
        err "Failed to download Astral installer from ${ASTRAL_INSTALLER_URL}"
        proxy_hints
        exit "$EXIT_NETWORK"
    fi

    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$installer" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$installer" | awk '{print $1}')"
    else
        die "$EXIT_INTERNAL" "Neither sha256sum nor shasum found." \
            "Cannot verify Astral installer integrity. Aborting for safety."
    fi
    if [ "$actual" != "$ASTRAL_INSTALLER_SHA256" ]; then
        die "$EXIT_INTERNAL" "SHA256 mismatch on Astral installer ${UV_VERSION}." \
            "expected: ${ASTRAL_INSTALLER_SHA256}" \
            "actual:   ${actual}" \
            "Refusing to execute. Re-run later or report at ${DOCS_URL}."
    fi

    UV_INSTALL_VERSION="$UV_VERSION" sh "$installer" >/dev/null 2>&1 || {
        err "Astral uv installer failed."
        proxy_hints
        exit "$EXIT_NETWORK"
    }

    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    export PATH

    if ! command -v uv >/dev/null 2>&1; then
        die "$EXIT_INTERNAL" "uv installed but not on PATH after bootstrap." \
            "Open a new shell and re-run, or add ~/.local/bin to PATH manually."
    fi
}

install_or_upgrade_worthless() {
    # First run: `uv tool install`. Re-run: that fails with "already installed",
    # so we fall through to `uv tool upgrade` for an idempotent upgrade path.
    if uv tool install "worthless==${WORTHLESS_VERSION}" >/dev/null 2>&1; then
        ok "  worthless ${WORTHLESS_VERSION} installed"
    elif uv tool upgrade worthless >/dev/null 2>&1; then
        ok "  worthless upgraded to ${WORTHLESS_VERSION}"
    else
        err "Failed to install worthless==${WORTHLESS_VERSION}."
        proxy_hints
        exit "$EXIT_NETWORK"
    fi
}

smoke_test() {
    # `uv run` works even before the user activates PATH — uv knows where
    # it put the binary.
    if ! uv run --no-project worthless --version >/dev/null 2>&1; then
        die "$EXIT_INTERNAL" "worthless installed but failed to run." \
            "Try: uv run --no-project worthless --version" \
            "Or:  worthless doctor"
    fi
}

# --- Per-shell PATH activation guidance --------------------------------------

print_activation_hint() {
    user_shell="$(basename "${SHELL:-/bin/sh}")"
    case "$user_shell" in
        bash|zsh)
            printf "  Activate in this shell: %s\n" 'export PATH="$HOME/.local/bin:$PATH"'
            printf "  Make permanent:         %s\n" \
                "echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.${user_shell}rc"
            ;;
        fish)
            printf "  Activate in this shell: %s\n" 'set -gx PATH $HOME/.local/bin $PATH'
            printf "  Make permanent:         %s\n" \
                'fish_add_path $HOME/.local/bin'
            ;;
        *)
            printf "  Activate in this shell: %s\n" 'export PATH="$HOME/.local/bin:$PATH"'
            printf "  (Detected shell: %s — adapt for your rc file)\n" "$user_shell"
            ;;
    esac
}

# --- Main --------------------------------------------------------------------

main() {
    setup_colors
    printf "\n"
    info "Worthless installer (uv-bootstrap)"
    printf "\n"

    detect_os
    ok "  Platform: ${OS}${IS_WSL:+ (WSL2)}"

    check_pipx_conflict

    ensure_uv
    install_or_upgrade_worthless
    smoke_test

    printf "\n"
    if command -v worthless >/dev/null 2>&1; then
        ok "Done! 'worthless' is on your PATH."
    else
        warn "Done — but 'worthless' is not yet on your PATH."
        printf "\n"
        print_activation_hint
    fi
    printf "\n"
    printf "  Get started:\n"
    printf "    ${BOLD}worthless enroll${RESET}     Set up your first API key\n"
    printf "    ${BOLD}worthless --help${RESET}     See all commands\n"
    printf "    ${BOLD}worthless doctor${RESET}     Run if anything looks off\n"
    printf "\n"
    printf "  Docs: %s\n\n" "$DOCS_URL"
}

IS_WSL=""
main "$@"
