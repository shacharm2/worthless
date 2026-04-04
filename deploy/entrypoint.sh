#!/bin/sh
set -e

HOME_DIR="${WORTHLESS_HOME:-/data}"

# Bootstrap on first boot only (idempotent but skips Python startup on restarts)
if [ ! -f "$HOME_DIR/fernet.key" ]; then
  python -c "from worthless.cli.bootstrap import get_home; get_home()"
  chmod 0400 "$HOME_DIR/fernet.key"
fi

# Pass Fernet key via file descriptor (not env var — env is visible in /proc)
exec 3< "$HOME_DIR/fernet.key"
export WORTHLESS_FERNET_FD=3

exec "$@"
