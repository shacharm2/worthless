#!/bin/sh
# Worthless installer — https://worthless.sh
# Usage: curl -sSL worthless.sh | sh
#
# POSIX sh compatible. No set -e — errors handled explicitly.

WORTHLESS_MIN_PYTHON="3.10"

# --- Colors (only if TTY) ---------------------------------------------------

setup_colors() {
    if [ -t 1 ]; then
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
warn()  { printf "${YELLOW}%s${RESET}\n" "$1"; }
err()   { printf "${RED}error: %s${RESET}\n" "$1" >&2; }
die()   { err "$1"; [ -n "$2" ] && printf "       %s\n" "$2" >&2; exit 1; }

# --- OS detection ------------------------------------------------------------

detect_os() {
    case "$(uname -s)" in
        Darwin)  OS="macos" ;;
        Linux)
            if [ -f /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null; then
                die "WSL detected. Install Worthless inside your Linux distribution directly:" \
                    "pip install worthless"
            fi
            OS="linux"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            die "Windows is not supported by this installer." \
                "Use: pip install worthless"
            ;;
        *)
            die "Unsupported OS: $(uname -s)" \
                "Use: pip install worthless"
            ;;
    esac
}

# --- Python detection --------------------------------------------------------

# Compare two version strings: returns 0 if $1 >= $2
version_gte() {
    # Split on dots and compare numerically
    major1=$(echo "$1" | cut -d. -f1)
    minor1=$(echo "$1" | cut -d. -f2)
    major2=$(echo "$2" | cut -d. -f1)
    minor2=$(echo "$2" | cut -d. -f2)
    if [ "$major1" -gt "$major2" ] 2>/dev/null; then return 0; fi
    if [ "$major1" -eq "$major2" ] 2>/dev/null && [ "$minor1" -ge "$minor2" ] 2>/dev/null; then return 0; fi
    return 1
}

find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
            if [ -n "$ver" ] && version_gte "$ver" "$WORTHLESS_MIN_PYTHON"; then
                PYTHON="$cmd"
                PYTHON_VERSION="$ver"
                return 0
            fi
        fi
    done
    return 1
}

# --- pipx detection / install ------------------------------------------------

find_pipx() {
    command -v pipx >/dev/null 2>&1
}

install_pipx() {
    if [ "$OS" = "macos" ] && command -v brew >/dev/null 2>&1; then
        info "Installing pipx via Homebrew..."
        brew install pipx >/dev/null 2>&1 || die "Failed to install pipx via Homebrew." \
            "Try: brew install pipx"
    else
        info "Installing pipx..."
        "$PYTHON" -m pip install --user pipx >/dev/null 2>&1 || die "Failed to install pipx." \
            "Try: $PYTHON -m pip install --user pipx"
    fi

    # Ensure pipx binaries are on PATH for this session
    "$PYTHON" -m pipx ensurepath >/dev/null 2>&1

    # Re-check — pipx may now be in a path we haven't sourced yet
    PIPX_CMD=""
    if command -v pipx >/dev/null 2>&1; then
        PIPX_CMD="pipx"
    elif "$PYTHON" -m pipx --version >/dev/null 2>&1; then
        PIPX_CMD="$PYTHON -m pipx"
    else
        die "pipx was installed but not found on PATH." \
            "Run: $PYTHON -m pipx ensurepath  then restart your shell."
    fi
}

# --- Main --------------------------------------------------------------------

main() {
    setup_colors
    printf "\n"
    info "Worthless Installer"
    printf "\n"

    # 1. OS
    detect_os

    # 2. Python
    if ! find_python; then
        err "Python ${WORTHLESS_MIN_PYTHON}+ not found."
        case "$OS" in
            macos) printf "       Install: %s\n" "brew install python3" >&2 ;;
            linux) printf "       Install: %s\n" "sudo apt install python3  (or your distro's package manager)" >&2 ;;
        esac
        exit 1
    fi
    ok "  Python ${PYTHON_VERSION} found"

    # 3. pipx
    if find_pipx; then
        PIPX_CMD="pipx"
        ok "  pipx found"
    else
        install_pipx
        ok "  pipx installed"
    fi

    # 4. Install worthless
    info "Installing worthless..."
    if $PIPX_CMD install worthless >/dev/null 2>&1 || $PIPX_CMD upgrade worthless >/dev/null 2>&1; then
        : # success
    else
        die "Failed to install worthless." \
            "Try: pipx install worthless"
    fi
    ok "  worthless installed"

    # 5. Verify on PATH
    if command -v worthless >/dev/null 2>&1; then
        ok "  worthless is on PATH"
    else
        warn "  worthless was installed but is not on PATH."
        printf "  Run: %s\n" "pipx ensurepath && exec \$SHELL"
        printf "\n"
        exit 0
    fi

    # 6. Success
    printf "\n"
    ok "Done! Get started:"
    printf "\n"
    printf "  ${BOLD}worthless enroll${RESET}     Set up your first API key\n"
    printf "  ${BOLD}worthless status${RESET}     Check installation\n"
    printf "  ${BOLD}worthless --help${RESET}     See all commands\n"
    printf "\n"
    printf "  Docs: https://docs.worthless.sh\n"
    printf "\n"
}

main "$@"
