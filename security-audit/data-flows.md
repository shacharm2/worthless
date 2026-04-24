# Sensitive Data and Trust Boundaries

## Sensitive values

The current implementation handles these values as security-relevant:

- full provider API key
- Shard A
- Shard B
- commitment
- nonce
- decoy key / rewritten `.env` value
- upstream auth header value
- Fernet key material for stored shard encryption

## Boundary map

### Local developer environment

Contains:

- `.env` inputs and rewritten decoys
- local shard material / metadata
- proxy process and daemon state
- wrapped child processes

### Local SQLite + encrypted shard store

Contains:

- encrypted Shard B
- enrollment/config metadata
- spend logs and related gating data

### In-memory proxy boundary

Contains:

- request metadata
- extracted Shard A from incoming provider-style headers
- decrypted shard material
- reconstructed full key for approved requests only

### Upstream provider boundary

Receives:

- provider-compatible HTTP request with the real upstream auth header after reconstruction

### MCP / management boundary

Can observe or influence aspects of the local protection system and therefore matters for trust modeling even though it is not the main request path

## Important current-shape conclusions

- Alias selection is path-based in the proxy route.
- Shard A is extracted from provider-style auth headers.
- Provider auth headers are created in the adapter layer.
- Metering is post-response, so usage enforcement and usage recording are separate loops.
- Process boundaries matter: `wrap`, daemon mode, and MCP all change how sensitive state exists in memory and how long it exists.
