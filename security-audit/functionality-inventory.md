# Functionality Inventory

## Core surfaces

### CLI surface

Primary responsibilities:

- detect and protect API keys from local `.env` files
- start and stop the local proxy
- wrap child processes so provider traffic routes through the proxy
- restore or revoke enrolled keys
- expose status and optional MCP integration

### Proxy surface

Primary responsibilities:

- parse alias and shard input from incoming requests
- run the active rules engine before reconstruction
- fetch/decrypt shard material and reconstruct upstream keys
- proxy provider requests and responses
- meter and record spend/usage

### Storage surface

Primary responsibilities:

- persist encrypted Shard B and enrollment metadata
- support fetch-before-decrypt semantics
- track decoy hashes and enrollment configuration

### Crypto surface

Primary responsibilities:

- split keys into shards
- reconstruct key material for approved requests
- expose mutable key-material types and zeroing helpers

### Adapter surface

Primary responsibilities:

- map provider-specific request conventions into the proxy model
- construct upstream auth headers
- normalize usage extraction support

### MCP surface

Primary responsibilities:

- expose management-oriented integration on top of the local protection model

## Security-relevant flow families

1. bootstrap and local-state initialization
2. key enrollment / lock and decoy rewriting
3. proxy request handling and metering
4. daemon lifecycle (`up`, `status`, `down`)
5. ephemeral wrap lifecycle (`wrap`)
6. recovery (`unlock`) and deletion (`revoke`)
7. MCP control/inspection surfaces

## Important current-shape conclusions

- The repo has more security-relevant state than the README alone implies.
- The runtime model is local-first and process/lifecycle-sensitive.
- The rules engine and metering path are central to the security story, not just operational details.
