# worthless-sh Worker

Cloudflare Worker at `worthless.sh` — serves `install.sh` to curl, redirects browsers to `wless.io`.

## Status

Scaffolding + RED tests only. **Implementation pending: WOR-300.**

## Contract (enforced by ./test)

| Request | Response |
|---------|----------|
| `curl` / `wget` / `fetch` / `Go-http-client` UA | `200` `text/plain`, body = `install.sh` |
| Chrome / Firefox / Safari UA | `302` → `https://wless.io` |
| Missing or unrecognized UA | `302` → `https://wless.io` (fail-safe) |
| `?explain=1` + curl UA | `200` `text/plain`, human-readable walkthrough |
| `?explain=1` + browser UA | `302` → `https://wless.io` |

## Run tests locally

```
cd workers/worthless-sh
npm install
npm test
```

All tests should be RED until the Worker is implemented in WOR-300.

## Host support matrix

The `install.sh` this Worker serves is validated on the hosts below via
`pytest -m docker` (see the root [README](../../README.md#installsh--worthlesssh-support-matrix)).

| Host | Status |
|---|---|
| Ubuntu 24.04 (bare / +uv) | Supported |
| Ubuntu 22.04 (bare) | Supported |
| Debian 12 (bare) | Supported |
| Alpine / musl | Experimental (expected to fail until PBS ships musl) |
| macOS | Supported (manual) |
| Native Windows | Not supported |

## Tickets

- WOR-305 — this test suite
- WOR-300 — Worker implementation (parent)
- WOR-304 — depends on `?explain=1` shipping
