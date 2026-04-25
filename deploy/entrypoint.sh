#!/bin/sh
# Entrypoint composes the uvicorn bind + proxy-header trust list from
# WORTHLESS_DEPLOY_MODE so the Dockerfile cannot accidentally hard-code
# a public bind. See WOR-345.
set -e

HOME_DIR="${WORTHLESS_HOME:-/data}"
FERNET_PATH="${WORTHLESS_FERNET_KEY_PATH:-$HOME_DIR/fernet.key}"
MODE="${WORTHLESS_DEPLOY_MODE:-loopback}"
PORT="${PORT:-8787}"

# Refuse silently-unsafe combinations before any Python startup.
# Exit 78 = configuration error (sysexits.h EX_CONFIG).
if [ "$MODE" = "public" ]; then
  if [ "${WORTHLESS_ALLOW_INSECURE:-}" = "true" ] || [ "${WORTHLESS_ALLOW_INSECURE:-}" = "1" ]; then
    echo "FATAL: WORTHLESS_ALLOW_INSECURE is forbidden when WORTHLESS_DEPLOY_MODE=public." >&2
    echo "       Set WORTHLESS_TRUSTED_PROXIES=<edge-CIDR> instead. See WOR-344." >&2
    exit 78
  fi
  if [ -z "${WORTHLESS_TRUSTED_PROXIES:-}" ]; then
    echo "FATAL: WORTHLESS_DEPLOY_MODE=public requires WORTHLESS_TRUSTED_PROXIES" >&2
    echo "       (CIDR of the edge layer, e.g. Render/Fly internal CIDR). See WOR-344." >&2
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

# Pass Fernet key via file descriptor (not env var — env is visible in /proc)
exec 3< "$FERNET_PATH"
export WORTHLESS_FERNET_FD=3

# Compose the uvicorn bind from deploy_mode. The Dockerfile CMD is now
# just the script-name + factory flag; bind + proxy-header trust live
# here where WORTHLESS_DEPLOY_MODE is in scope.
case "$MODE" in
  public)
    HOST="0.0.0.0"
    set -- uvicorn worthless.proxy.app:create_app --factory \
      --host "$HOST" --port "$PORT" \
      --proxy-headers --forwarded-allow-ips="$WORTHLESS_TRUSTED_PROXIES"
    ;;
  lan)
    HOST="${WORTHLESS_HOST:-0.0.0.0}"
    if [ -n "${WORTHLESS_TRUSTED_PROXIES:-}" ]; then
      set -- uvicorn worthless.proxy.app:create_app --factory \
        --host "$HOST" --port "$PORT" \
        --proxy-headers --forwarded-allow-ips="$WORTHLESS_TRUSTED_PROXIES"
    else
      set -- uvicorn worthless.proxy.app:create_app --factory \
        --host "$HOST" --port "$PORT"
    fi
    ;;
  loopback)
    HOST="${WORTHLESS_HOST:-127.0.0.1}"
    set -- uvicorn worthless.proxy.app:create_app --factory \
      --host "$HOST" --port "$PORT"
    ;;
  *)
    echo "FATAL: unknown WORTHLESS_DEPLOY_MODE=$MODE (expected loopback|lan|public)" >&2
    exit 78
    ;;
esac

exec "$@"
