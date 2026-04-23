#!/bin/sh
# Post-install verification — run inside the test container after install.sh.
# Asserts more than `worthless --version`: exit codes, stderr cleanliness,
# resolved binary path, and that `--help` loads the subcommand registry
# (catches lazy-import failures that --version sidesteps).
set -eu

export PATH="$HOME/.local/bin:$PATH"

# Resolve path; must live under $HOME/.local/bin (not a system shim).
WORTHLESS_PATH=$(command -v worthless)
case "$WORTHLESS_PATH" in
    "$HOME/.local/bin/worthless") ;;
    *) echo "FAIL: worthless resolved to $WORTHLESS_PATH, expected $HOME/.local/bin/worthless" >&2; exit 1 ;;
esac

# --version must exit 0 and contain "worthless".
VERSION_OUT=$(worthless --version 2>/tmp/version.err)
echo "$VERSION_OUT"
if ! echo "$VERSION_OUT" | grep -qi "worthless"; then
    echo "FAIL: --version output missing 'worthless': $VERSION_OUT" >&2
    exit 1
fi
if grep -qi "Traceback\|ModuleNotFoundError" /tmp/version.err; then
    echo "FAIL: --version emitted Traceback/ModuleNotFoundError" >&2
    cat /tmp/version.err >&2
    exit 1
fi

# --help must exit 0 (exercises lazy subcommand imports — cryptography, keyring).
if ! worthless --help >/tmp/help.out 2>/tmp/help.err; then
    echo "FAIL: worthless --help exited non-zero" >&2
    cat /tmp/help.err >&2
    exit 1
fi
if grep -qi "Traceback\|ModuleNotFoundError" /tmp/help.err; then
    echo "FAIL: --help emitted Traceback/ModuleNotFoundError" >&2
    cat /tmp/help.err >&2
    exit 1
fi

echo "OK: install verified at $WORTHLESS_PATH"
