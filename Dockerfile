# NOTE: pin patch versions; update quarterly or on CVE.
FROM python:3.13-slim-bookworm@sha256:f13a6b7565175da40695e8109f64cbc4d2e65f4c9ef2e3b321c3a44fa3c06fe7 AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir .

# -----------------------------------------------------------
FROM python:3.13-slim-bookworm@sha256:f13a6b7565175da40695e8109f64cbc4d2e65f4c9ef2e3b321c3a44fa3c06fe7

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r -g 10001 worthless \
    && useradd -r -u 10001 -g worthless -d /data -s /sbin/nologin worthless-proxy \
    && useradd -r -u 10002 -g worthless -d /nonexistent -s /sbin/nologin worthless-crypto \
    && mkdir -p /data /secrets /run/worthless \
    && chown worthless-proxy:worthless /data /secrets \
    && chown root:worthless /run/worthless \
    && chmod 0770 /run/worthless

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/worthless /usr/local/bin/worthless
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY deploy/entrypoint.sh /entrypoint.sh
COPY deploy/start.py /deploy/start.py

# HOME=/data so Path.home() resolves to the writable /data volume, not
# /home/worthless on the read-only root. The user-provider registry
# (`worthless providers register` writes ~/.worthless/providers.toml)
# would otherwise fail mid-write under read_only:true. 8rqs's lock-time
# URL validation (M3) makes the registry mandatory for non-bundled
# upstreams, so this is now a hard correctness need.
ENV WORTHLESS_HOME=/data \
    WORTHLESS_DB_PATH=/data/worthless.db \
    WORTHLESS_SHARD_A_DIR=/data/shard_a \
    HOME=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8787

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/healthz')"

# WOR-310: no static USER directive — container starts as root so deploy/start.py
# can spawn the sidecar as worthless-crypto (uid 10002) and drop self to
# worthless-proxy (uid 10001) before exec uvicorn. A pre-dropped uid cannot
# call setresuid() to a different uid; the runtime priv-drop dance requires
# the entrypoint to begin as root.

# Self-documenting security contract: docker inspect surfaces these so
# operators see what flags the security claim depends on.
#
# Capability note: ``--cap-drop=ALL`` is INCOMPATIBLE with the WOR-310
# runtime priv-drop dance — six caps are needed *briefly* during
# entrypoint bootstrap + the priv-drop dance, all cleared by the
# preexec_fn before exec so the post-drop process has zero caps:
#   * SETUID / SETGID — setresuid/setresgid/setgroups
#   * SETPCAP        — prctl(PR_CAPBSET_DROP)
#   * DAC_OVERRIDE   — entrypoint bootstrap writes into /data, which
#                      is owned by worthless-proxy (uid 10001);
#                      without DAC_OVERRIDE root is treated as "other"
#                      and mkdir /data/shard_a hits EACCES.
#   * CHOWN          — chown bootstrap output to worthless-proxy
#                      after first boot.
#   * FOWNER         — chmod fernet.key to 0400 (non-root-owned file).
# Phase C's priv-drop achieves the SAME end-state as ``--cap-drop=ALL``
# because the preexec_fn calls ``prctl(PR_CAPBSET_DROP, cap)`` for
# cap 0..63 immediately before setresuid — by the time uvicorn execs,
# the bounding set is empty.
LABEL org.worthless.required-run-flags="--security-opt=no-new-privileges"
LABEL org.worthless.recommended-run-flags="--read-only --tmpfs /tmp --cap-drop=ALL --cap-add=SETUID --cap-add=SETGID --cap-add=SETPCAP --cap-add=DAC_OVERRIDE --cap-add=CHOWN --cap-add=FOWNER"

# Bind/host live in entrypoint.sh — don't re-add `--host` here, it bypasses deploy_mode.
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
# No CMD: deploy/start.py runs the full lifecycle (split + spawn sidecar +
# exec uvicorn) — overriding the command would skip sidecar spawn and break
# the proxy's IPC contract.
