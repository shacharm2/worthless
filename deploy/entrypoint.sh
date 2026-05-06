#!/bin/sh
# Composes uvicorn bind + proxy-header trust list from WORTHLESS_DEPLOY_MODE.
# Single-container lifecycle: deploy/start.py runs split_to_tmpfs +
# spawn_sidecar and then execs uvicorn so tini supervises both processes as
# siblings. The proxy requires an IPC peer and refuses to start without one,
# so a plain ``exec uvicorn`` would never bind.
set -e

HOME_DIR="${WORTHLESS_HOME:-/data}"
FERNET_PATH="${WORTHLESS_FERNET_KEY_PATH:-$HOME_DIR/fernet.key}"
MODE="${WORTHLESS_DEPLOY_MODE:-loopback}"
PORT="${PORT:-8787}"

# Refuse unsafe combinations before Python startup. Exit 78 = sysexits EX_CONFIG.
case "$MODE" in
  loopback|lan|public)
    ;;
  *)
    echo "FATAL: unknown WORTHLESS_DEPLOY_MODE=$MODE (expected loopback|lan|public)" >&2
    exit 78
    ;;
esac

if [ "$MODE" = "public" ]; then
  if [ "${WORTHLESS_ALLOW_INSECURE:-}" = "true" ] || [ "${WORTHLESS_ALLOW_INSECURE:-}" = "1" ]; then
    echo "FATAL: WORTHLESS_ALLOW_INSECURE is forbidden when WORTHLESS_DEPLOY_MODE=public." >&2
    echo "       Set WORTHLESS_TRUSTED_PROXIES=<edge-CIDR> instead." >&2
    exit 78
  fi
  if [ -z "${WORTHLESS_TRUSTED_PROXIES:-}" ]; then
    echo "FATAL: WORTHLESS_DEPLOY_MODE=public requires WORTHLESS_TRUSTED_PROXIES" >&2
    echo "       (CIDR of the edge layer, e.g. Render/Fly internal CIDR)." >&2
    exit 78
  fi
fi

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

# WORTHLESS_DEPLOY_MODE / WORTHLESS_TRUSTED_PROXIES / WORTHLESS_HOST flow
# through the environment into deploy/start.py, which composes the uvicorn
# argv (host + --proxy-headers + --forwarded-allow-ips) before exec.
export WORTHLESS_DEPLOY_MODE="$MODE"
export PORT="$PORT"

exec python /deploy/start.py
