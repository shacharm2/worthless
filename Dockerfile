# NOTE: pin patch versions; update quarterly or on CVE.
FROM python:3.12.9-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir .

# -----------------------------------------------------------
FROM python:3.12.9-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r worthless && useradd -r -g worthless -m worthless \
    && mkdir -p /data && chown worthless:worthless /data

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/worthless /usr/local/bin/worthless
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY deploy/entrypoint.sh /entrypoint.sh

ENV WORTHLESS_HOME=/data \
    WORTHLESS_DB_PATH=/data/worthless.db \
    WORTHLESS_SHARD_A_DIR=/data/shard_a \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8787

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/healthz')"

USER worthless

ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn worthless.proxy.app:create_app --factory --host 0.0.0.0 --port ${PORT}"]
