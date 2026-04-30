#!/bin/sh
set -e

HOME_DIR="${WORTHLESS_HOME:-/data}"
FERNET_PATH="${WORTHLESS_FERNET_KEY_PATH:-$HOME_DIR/fernet.key}"

# Migrate fernet.key to a separate volume when WORTHLESS_FERNET_KEY_PATH is
# explicitly set (e.g., docker-compose with a secrets volume).  Without the
# env var the key stays on the data volume — safe for single-volume PaaS.
if [ -n "$WORTHLESS_FERNET_KEY_PATH" ] && [ ! -f "$FERNET_PATH" ] && [ -f "$HOME_DIR/fernet.key" ]; then
  install -m 0400 "$HOME_DIR/fernet.key" "$FERNET_PATH"
  rm "$HOME_DIR/fernet.key"
fi

# Bootstrap on first boot only (idempotent but skips Python startup on restarts)
if [ ! -f "$FERNET_PATH" ]; then
  python -c "from worthless.cli.bootstrap import get_home; get_home()"
  # Lock down fernet.key after bootstrap — can't use umask 0377 during
  # bootstrap because SQLite WAL/SHM files also get created and need
  # to be writable.
  chmod 0400 "$FERNET_PATH"
fi

# Pass Fernet key via file descriptor (not env var — env is visible in /proc).
# deploy/start.py reads it back to drive the lifecycle setup.
exec 3< "$FERNET_PATH"
export WORTHLESS_FERNET_FD=3

# Single-container post-WOR-309 lifecycle: deploy/start.py runs split_to_tmpfs +
# spawn_sidecar and then execs uvicorn so tini supervises both processes as
# siblings. Plain ``exec "$@"`` (just uvicorn) was the legacy path; the proxy
# now requires an IPC peer and refuses to start without one.
exec python /deploy/start.py
