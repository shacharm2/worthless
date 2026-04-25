# Operator Hardening Guide

Date: 2026-03-30
Applies to: current Python PoC

## Threat Boundary

Worthless is strongest as a local guardrail or tightly controlled internal
service. It is not safe to treat the current proxy as an internet-facing
authenticated API gateway.

If you remember only one thing, remember this:

Do not let the proxy origin be directly reachable by untrusted clients.

## Non-Negotiables

1. Bind the proxy only to loopback or a private interface.
2. Put a trusted edge in front of it and strip or overwrite inbound `X-Forwarded-*` headers.
3. Do not store server-readable Shard A on any remotely reachable proxy.
4. Require an explicit client authentication layer before requests reach Worthless.
5. Treat same-user code execution on developer machines as equivalent to secret loss.

## Local Developer Mode

Use this mode when the proxy exists only to protect a single developer or agent
running on the same host.

Required controls:

- Keep the proxy on `127.0.0.1` only.
- Do not expose the loopback port through tunnels or reverse proxies.
- Avoid running untrusted packages, plugins, or agent tools as the same user.
- Keep `~/.worthless` out of backup systems that other principals can read.
- Clear proxy-related env vars before launch.

Recommended shell hygiene:

- Unset `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`
- Avoid exporting `WORTHLESS_FERNET_KEY` directly into long-lived shells
- Prefer short-lived process-scoped env or inherited file descriptors

## Self-Hosted / Team Deployments

Treat the current Python service as an internal component, not the internet edge.

Required controls:

- Terminate TLS at a hardened edge proxy or load balancer.
- Allow traffic to the Worthless origin only from that trusted edge.
- Enforce client auth before the request reaches Worthless.
- Overwrite `X-Forwarded-Proto` at the edge; never trust user-supplied values.
- Disable alias inference for remote paths or require explicit alias plus client-held Shard A.
- Do not mount or sync server-readable Shard A for remotely reachable instances.

Strongly recommended:

- Put the service on a private network segment.
- Add mTLS or equivalent client identity.
- Add per-tenant auth separate from provider credential handling.
- Add connection limits and edge request size limits.
- Add rate limiting at the edge, not only in-process.

## Storage and Host Hardening

- Keep `fernet.key`, the DB, and `shard_a` under a dedicated service account.
- Restrict file permissions to owner-only access.
- Encrypt disks and backups.
- Decide whether SQLite WAL files are acceptable in your backup and forensic model.
- Disable core dumps and interactive debugging on production instances.
- Avoid colocating other workloads in the same container or pod.

## Logging and Telemetry

Never log:

- Provider API keys
- Shard material
- `x-worthless-*` headers
- Raw request or response bodies unless heavily redacted

Alert on:

- Unexpected direct traffic to the origin
- Sudden increases in long-lived streams
- Spend records stuck at zero during heavy usage
- Spikes in 402, 429, 502, and 504 responses
- Origin requests with forged or unexpected forwarding headers

## Supply-Chain Hygiene

Do not rely on:

- `curl | sh`
- unpinned `npx -y`
- unconstrained dependency resolution for production installs

Prefer:

- signed release artifacts
- pinned package versions
- reproducible builds
- explicit checksums
- an internal package mirror for team rollouts

## Claim Discipline

Do not tell operators:

- "Your key is now worthless to steal"
- "This is a hard spend cap"
- "One half always stays on your machine"

Tell operators instead:

- "This materially reduces the value of leaked API keys in specific deployment modes."
- "Current enforcement is strongest for controlled local and internal deployments."
- "The Python PoC has known memory and accounting limitations."

## Immediate Hardening Backlog

1. Remove path-based alias inference for any non-local deployment.
2. Remove file-based Shard A fallback from remotely reachable proxies.
3. Stop trusting raw `X-Forwarded-Proto`.
4. Disable ambient transport env trust for upstream HTTP clients.
5. Replace token-only accounting with provider-correct spend accounting.
6. Enforce request and stream resource limits at both edge and app layers.
