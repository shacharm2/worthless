I need Write permission to create the file. The user explicitly asked me to write to `/Users/shachar/Projects/worthless/worthless/docs/research/competitive-landscape.md`. I also lack WebSearch (so the analysis is from my training knowledge through May 2025) and Grep permissions.

Let me provide the full analysis directly since I cannot write the file. The user or parent agent can save it.

---

# Competitive Landscape: API Key Security & Management

> Last updated: 2026-04-08. Based on publicly available product information through early 2025. Pricing and features may have changed.

## Competitors

### Infisical (Secrets Management)

**What they do:** Open-source secrets management platform that centralizes secrets across infrastructure with automatic syncing to apps, CI/CD, and cloud services.

**Key security features:** End-to-end encrypted storage (client-side encryption), secret rotation, RBAC, audit logs, IP allowlisting, secret versioning.

**Pricing:** Open-source self-hosted free. Cloud free up to 5 users, Pro ~$6/user/month, Enterprise custom.

**What they DON'T do that Worthless does:** No split-key architecture (secrets stored whole, encrypted but whole). No client-side key splitting. No gate-before-reconstruct. No per-request spend enforcement. No LLM-aware proxy.

**What they do that Worthless doesn't:** General-purpose secrets management (DB creds, certificates, etc.), secret rotation automation, full SDLC integration (CI/CD, IaC, containers), secret scanning/leak detection, multi-environment management.

---

### Doppler (Secrets Management)

**What they do:** Universal secrets manager replacing .env files with centralized sync to every environment and service.

**Key security features:** Encryption at rest and in transit, activity logs/audit trails, secret referencing/composition, CLI and SDK access.

**Pricing:** Free tier (5 users, unlimited secrets), Team $4-6/user/month, Enterprise custom.

**What they DON'T do that Worthless does:** No split-key. No client-side splitting. No gate-before-reconstruct. No per-request spend caps. No LLM-aware proxy.

**What they do that Worthless doesn't:** Universal secrets for all types, automatic environment syncing (deploy-time injection), dashboard, secret referencing/composition, broad platform integrations (Vercel, Netlify, Railway).

---

### HashiCorp Vault (Secrets/Key Management)

**What they do:** Industry-standard secrets management providing dynamic secrets, encryption-as-a-service, and identity-based access.

**Key security features:** Dynamic secrets (short-lived, auto-revoked), Transit encryption engine, Shamir's sharing for Vault's own unseal key (not stored secrets), identity-based policies, tamper-evident audit logs, auto-unseal with cloud KMS.

**Pricing:** OSS free self-managed. HCP Vault ~$0.03/hr small clusters. HCP Vault Dedicated ~$1.58/hr. Enterprise self-managed custom.

**What they DON'T do that Worthless does:** Vault uses Shamir for its own unseal key, NOT for stored secrets -- each API key is stored whole once unsealed. No client-side splitting of API keys. No gate-before-reconstruct at request level. No per-request spend enforcement. No LLM-aware proxy.

**What they do that Worthless doesn't:** Dynamic secret generation (ephemeral DB creds, cloud IAM), encryption-as-a-service, PKI/certificate management, identity broker (OIDC, LDAP, cloud IAM federation), lease management/auto-revocation, massive ecosystem.

---

### LiteLLM Proxy (LLM Proxy)

**What they do:** Open-source LLM proxy providing a unified OpenAI-compatible API across 100+ providers with virtual keys, spend tracking, and rate limiting.

**Key security features:** Virtual API keys (proxy keys map to real provider keys on server), per-key and per-user spend limits, rate limiting, audit logging, admin dashboard.

**Pricing:** Open-source self-hosted free. Enterprise hosted custom.

**What they DON'T do that Worthless does:** No split-key (real API keys stored whole on server). No client-side splitting. No gate-before-reconstruct (keys available in memory for every request). Server breach exposes all provider keys.

**What they do that Worthless doesn't:** Multi-provider routing (100+ LLMs), model fallback chains, load balancing across keys, unified API format, caching layer, prompt/response logging.

---

### Portkey (AI Gateway)

**What they do:** AI gateway and observability platform with unified LLM API, reliability features, guardrails, caching, and cost management.

**Key security features:** Virtual keys (Portkey stores real keys, issues proxy tokens), per-key budget limits, request/response guardrails (PII detection), rate limiting.

**Pricing:** Free tier (10K requests/month), Growth ~$49/month, Enterprise custom. Cloud-hosted primarily.

**What they DON'T do that Worthless does:** No split-key. No client-side splitting. You hand your API key to Portkey's cloud. No gate-before-reconstruct. No self-hosted option for key storage.

**What they do that Worthless doesn't:** Content guardrails (PII filtering, topic blocking), prompt management/A/B testing, full observability (latency, tokens, costs, traces), semantic caching, multi-provider routing, managed cloud (no infra to run).

---

### Helicone (LLM Observability/Proxy)

**What they do:** LLM observability and proxy platform focused on logging, monitoring, and analyzing LLM usage with cost tracking.

**Key security features:** Proxy mode with key management, per-user/per-key rate limiting, cost tracking/budget alerts, prompt threat detection.

**Pricing:** Free tier (100K requests/month), Pro ~$20-80/month, Enterprise custom. Primarily cloud-hosted.

**What they DON'T do that Worthless does:** No split-key. No client-side splitting. No gate-before-reconstruct. Budget alerts are informational, not enforcement gates preventing key reconstruction.

**What they do that Worthless doesn't:** Deep observability (latency percentiles, token distributions, cost breakdowns), request/response logging/replay, prompt analytics/evaluation, user session tracking, experiment tracking, dashboards.

---

### Martian (AI Gateway)

**What they do:** AI gateway focused on intelligent model routing -- automatically selects the best LLM per prompt based on cost, quality, and latency.

**Key security features:** Centralized key management, fallback/retry logic.

**Pricing:** Usage-based with per-request markup. Enterprise custom.

**What they DON'T do that Worthless does:** No split-key. No client-side splitting. No gate-before-reconstruct. No hard spend cap enforcement. No cryptographic key protection.

**What they do that Worthless doesn't:** Intelligent model routing (quality-aware), automatic model selection by prompt complexity, cost optimization via smart routing, model benchmarking.

---

### OpenRouter (LLM Routing)

**What they do:** Unified LLM API routing requests to cheapest/best provider with single key and billing account.

**Key security features:** Single key replaces all provider keys, credit-based spending limits, rate limiting, model availability monitoring.

**Pricing:** Pay-per-use with ~10-20% markup. Free tier for some models. No subscription.

**What they DON'T do that Worthless does:** No split-key. No client-side splitting. Trust model is "trust OpenRouter entirely." No self-hosted option. No BYOK -- you use their keys.

**What they do that Worthless doesn't:** Eliminate need for individual provider accounts, unified billing, model marketplace with community ratings, free model access, automatic failover, OAuth for end-users.

---

## Summary: Differentiation Analysis

### Where Worthless Is Genuinely Differentiated

No competitor implements split-key architecture for API keys. This is the core moat:

| Capability | Worthless | Vault | Infisical | Doppler | LiteLLM | Portkey | Helicone | Martian | OpenRouter |
|---|---|---|---|---|---|---|---|---|---|
| Client-side key splitting | Yes | No | No | No | No | No | No | No | No |
| Gate-before-reconstruct | Yes | No | No | No | No | No | No | No | No |
| Key never stored whole on server | Yes | No | No | No | No | No | No | No | No |
| Server breach = keys safe | Yes | No | No | No | No | No | No | No | No |
| Per-request spend enforcement | Yes | No | No | No | Partial | Partial | Alerts only | No | Partial |

**1. Zero-knowledge key storage.** Every competitor stores complete API keys somewhere. Worthless never has the complete key at rest.

**2. Gate-before-reconstruct.** Even LiteLLM and Portkey check budget then use an already-available key. Worthless checks budget before the key can even form. Denied request = zero key material in memory.

**3. Server breach resilience.** Compromising any competitor's server yields usable keys. Compromising Worthless's server yields Shard B -- cryptographically useless without Shard A (which never touches the server).

### Where Worthless Overlaps

**1. Spend controls.** LiteLLM, Portkey, OpenRouter all cap budgets. Worthless's enforcement is cryptographically stronger but the user-facing result ("budget capped") looks similar. Differentiation is in threat model, not UX.

**2. Proxy architecture.** Multiple competitors are LLM proxies. Users may compare on proxy features (latency, reliability, multi-provider) where Worthless is narrower.

**3. Key management.** Vault/Infisical/Doppler manage secrets. Users will ask "why not Vault?" The answer is architectural but requires education.

### Where Worthless Has Gaps

| Gap | Who does it | Impact |
|---|---|---|
| Multi-provider routing/fallback | LiteLLM, Portkey, Martian, OpenRouter | Users wanting one tool for security AND routing may choose a gateway |
| General secrets management | Vault, Infisical, Doppler | Worthless only handles API keys |
| LLM observability | Helicone, Portkey | No request/response logging or prompt analytics |
| Content guardrails | Portkey | No PII detection or prompt injection protection |
| Semantic caching | Portkey, LiteLLM | No response caching |
| Dynamic secret generation | Vault | No ephemeral, auto-revoked credentials |
| Managed cloud service | Portkey, Helicone, OpenRouter, Doppler | V1 is self-hosted only |

### Strategic Positioning

Worthless is the only tool treating API key security as a cryptographic problem rather than an access control problem. Every competitor's model: "restrict who can read the secret." Worthless's model: "the secret doesn't exist until the moment it's needed, and only if policy allows."

**Primary narrative:** "You trust Vault/Infisical/Doppler to never get breached. You trust LiteLLM/Portkey to never leak your keys. Worthless doesn't ask for trust -- even we can't see your keys."

**Biggest risk:** AI gateways (LiteLLM, Portkey) with "good enough" spend controls. Most teams pick the tool solving the most problems (routing + observability + budgets) over the strongest security model. Worthless needs to either integrate with these gateways or build enough proxy features to stand alone.

**Recommended positioning:** Worthless is not a secrets manager and not an AI gateway. It is a **security layer** that can sit in front of either. The split-key proxy can be composed with LiteLLM or Portkey rather than replacing them.

---

I was unable to write this to `/Users/shachar/Projects/worthless/worthless/docs/research/competitive-landscape.md` because the Write tool permission was denied. The parent agent or user will need to save this content to that path.
