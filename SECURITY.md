# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| v0.x (Python PoC) | Yes — security fixes applied |

## Reporting a Vulnerability

I take security seriously. Worthless exists to protect API keys — a vulnerability here has real consequences.

### How to Report

- **Preferred:** [GitHub Private Vulnerability Reporting](https://github.com/shacharm2/worthless/security/advisories/new)
- **Alternative:** Email `security@wless.io`

### Response Timeline

This is a solo, open-source project. I aim to acknowledge reports quickly and triage as fast as I reasonably can, but all timelines are best-effort and not guaranteed. I follow coordinated disclosure and target public disclosure within roughly 90 days, adjusted to severity and to giving a fix time to land.

### Scope

Vulnerabilities in the following areas are in scope:

- **Crypto** — key splitting, reconstruction, commitment scheme, zeroing
- **Proxy** — gate-before-reconstruct bypass, request smuggling, error leakage
- **Storage** — shard encryption at rest, repository access controls
- **CLI** — credential handling, shard exposure, command injection

### Out of Scope

- Denial of service against the self-hosted proxy (it's your infrastructure)
- Social engineering
- Attacks requiring physical access to the host machine
- Issues in dependencies (report upstream; I'll update promptly)

## Testing Guidelines

As an open-source project, you are encouraged to audit and test the code. Please ensure you:

- Only test against infrastructure and accounts that you own or have explicit permission to test against.
- Report vulnerabilities through the channels above before discussing them publicly, giving me time to patch the code.

## Preferred Languages

English.

## Security Posture

For a complete threat model, architectural invariants, known limitations, and confidence levels, see [SECURITY_POSTURE.md](SECURITY_POSTURE.md).
