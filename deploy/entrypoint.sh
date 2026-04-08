#!/bin/sh
set -e

HOME_DIR="${WORTHLESS_HOME:-/data}"
FERNET_PATH="${WORTHLESS_FERNET_KEY_PATH:-$HOME_DIR/fernet.key}"

# Migrate fernet.key from data volume to secrets volume (one-time upgrade).
# Existing deployments stored fernet.key alongside shard data on the same
# volume — move it so the encryption key and encrypted shards are separated.
if [ ! -f "$FERNET_PATH" ] && [ -f "$HOME_DIR/fernet.key" ] && [ "$FERNET_PATH" != "$HOME_DIR/fernet.key" ]; then
  cp "$HOME_DIR/fernet.key" "$FERNET_PATH"
  chmod 0400 "$FERNET_PATH"
  rm "$HOME_DIR/fernet.key"
fi

# Bootstrap on first boot only (idempotent but skips Python startup on restarts)
if [ ! -f "$FERNET_PATH" ]; then
  python -c "from worthless.cli.bootstrap import get_home; get_home()"
  chmod 0400 "$FERNET_PATH"
fi

# Pass Fernet key via file descriptor (not env var — env is visible in /proc)
exec 3< "$FERNET_PATH"
export WORTHLESS_FERNET_FD=3

exec "$@"
