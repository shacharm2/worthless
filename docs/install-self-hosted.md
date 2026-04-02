# Install -- Self-Hosted

> [!NOTE]
> **Planned** -- Self-hosted deployment (Docker Compose, Helm charts) is not yet
> available. The documentation below describes the target-state design.
> See the [README](../README.md) for current install options.

## Target-state deployment

A Docker Compose stack with proxy + PostgreSQL for production self-hosted deployments.

- Proxy listens on port 8787
- Shard B encrypted at rest
- Helm charts and Terraform modules in `deploy/`
- Your infrastructure, your data, your control

## Current option

Run the proxy locally from source:

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv pip install -e .
worthless lock
worthless up
```

The proxy runs on `localhost:8787`. See the [README](../README.md) for details.
