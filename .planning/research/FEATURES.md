# Feature Landscape

**Domain:** API key security proxy for LLM providers
**Researched:** 2026-03-14

## Table Stakes

Features users expect. Missing = product feels incomplete or untrustworthy.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Transparent proxying | Every competitor (Portkey, Helicone, LiteLLM) proxies requests without code changes. Users will not rewrite their app. | Medium | Must intercept calls to `api.openai.com`, `api.anthropic.com` and forward upstream. OpenAI-compatible format is the lingua franca. |
| Multi-provider support (OpenAI + Anthropic) | These two cover ~80% of developer LLM usage. Supporting only one is a toy. | Medium | Different auth header formats (`Authorization: Bearer` vs `x-api-key`). Must handle both. |
| CLI-based setup | LiteLLM, Infisical, and modern dev tools all offer CLI onboarding. Devs expect `pip install && command` not "edit 3 config files." | Low | Worthless already plans `worthless enroll` + `worthless wrap`. This is correct. |
| Sub-2-minute install-to-working | Aikido, Sealos, and others benchmark "minutes to value." Worthless targets 90 seconds. Anything over 5 minutes and solo devs bail. | Low | Mostly a UX/documentation concern, not a code complexity issue. |
| Key never in plaintext at rest | This is the *entire value proposition*. Infisical encrypts at rest with XChaCha20. Vault uses transit encryption. Helicone uses column encryption. Users expect keys to not sit in `.env` files. | High | XOR split-key is Worthless's approach. The split must be cryptographically sound (random shard + XOR). |
| HTTPS/TLS for upstream calls | Any proxy that sends keys over plaintext HTTP is a liability. Users assume encryption in transit. | Low | Use standard TLS via `httpx` or `aiohttp`. Non-negotiable. |
| Request forwarding fidelity | Headers, streaming (SSE), request bodies, and response bodies must pass through unmodified. Broken streaming = broken product. | High | SSE streaming for chat completions is critical. Portkey, Helicone, LiteLLM all handle this. Must support `stream: true`. |
| Error transparency | When upstream returns 429, 500, or auth errors, the proxy must pass them through clearly, not swallow them into generic 502s. | Low | Map upstream errors faithfully. Add proxy-specific errors only for proxy-specific failures. |
| Graceful startup/shutdown | Proxy must not corrupt state or drop in-flight requests on SIGTERM. | Low | Standard async server lifecycle management. |
| Status/health check endpoint | Devs need to verify the proxy is running. `curl localhost:PORT/health` is universal. | Low | Return proxy status, not upstream status. |

## Differentiators

Features that set Worthless apart. Not expected by users of existing tools, but uniquely valuable.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Split-key architecture (XOR secret sharing)** | NO competitor does this. Portkey uses "virtual keys" (still stores the real key). Helicone vaults keys (still stores the real key encrypted). Infisical encrypts keys (still stores the real key encrypted). Worthless *eliminates* the complete key from any single location. This is the moat. | High | Two shards: client-side (Shard A) + server-side (Shard B). XOR reconstruction per-request, in-memory only, never written to disk. Keyxor on GitHub validates the XOR approach is sound. |
| **Gate-before-reconstruct invariant** | Spend cap / rate limit / policy check happens BEFORE the key is ever reassembled. No other tool enforces this ordering guarantee. If the gate says no, the key literally never exists. | Medium | Requires the proxy to evaluate policy before XOR reconstruction. Architecture must enforce this ordering -- it's not just a feature, it's a security property. |
| **"Stolen? So what." security model** | Competitors say "we protect your key." Worthless says "the key doesn't exist to steal." Fundamentally different mental model. If server is breached, attacker gets Shard B (worthless alone). If client `.env` is leaked, attacker gets Shard A (worthless alone). | High | This is marketing AND architecture. The split must be cryptographically sound -- random Shard B, Shard A = Key XOR Shard B. |
| **Hard spend caps at proxy level** | Portkey has budget limits but only for Enterprise/Pro. LiteLLM has hierarchical budgets but requires their full platform. Worthless can offer hard caps that literally prevent reconstruction if budget is exceeded. | Medium | Requires local spend tracking (SQLite for PoC). "Hard" cap means the request is rejected, not just logged. Future: hosted proxy for real-time cross-device caps. |
| **Zero cloud dependency (local-first)** | Helicone is cloud-first. Portkey is cloud-first. LiteLLM can run locally but setup is heavy (Docker + Postgres). Worthless runs on localhost out of the box. | Low | This is a constraint that becomes an advantage for privacy-conscious devs. No data leaves the machine. |
| **Stack-agnostic via env var wrapping** | Most competitors require SDK integration (import their library). Worthless works by rewriting `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` to point at localhost proxy. Any language, any SDK, any framework. | Low | `worthless wrap` sets env vars. This is simpler than SDK integration and works with tools like `curl`, `httpx`, LangChain, etc. |
| **Honeypot mode** | The enrolled key becomes a tripwire. If someone steals the split shard and tries to use it directly against the provider, it fails AND can be detected. The "worthless" key IS the canary. | Medium | Future feature. Requires logging failed direct-use attempts. Not in PoC scope but architecturally enabled by the split. |

## Anti-Features

Features to explicitly NOT build. These are traps that would dilute focus or compromise the security model.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Dashboard UI** | Every competitor has one. Building a dashboard is 3x the work of the core proxy and shifts focus from "security tool" to "observability platform." Worthless is a CLI tool, not a SaaS. | Terminal output. `worthless status` shows what matters. Dashboard is future scope only after core is proven. |
| **SDK / library integration** | Portkey and Helicone require `from portkey import ...`. This creates vendor lock-in, limits language support, and is antithetical to "stack-agnostic." | Env var rewriting (`OPENAI_BASE_URL=http://localhost:PORT`). Works with every SDK in every language without code changes. |
| **Response caching / semantic caching** | Helicone's semantic caching is impressive but irrelevant to security. Caching LLM responses in a security proxy means the proxy now stores sensitive data (prompts and completions). This violates the "proxy stores nothing valuable" principle. | Don't cache. Forward and forget. If users want caching, they can add Helicone or similar upstream of Worthless. |
| **Load balancing / routing** | LiteLLM and Portkey offer multi-model routing. This is an LLM gateway feature, not a security feature. Adding routing complexity increases attack surface. | Single upstream per enrolled key. If users need routing, use LiteLLM as the upstream and Worthless as the security layer in front. |
| **Content filtering / guardrails** | Portkey offers guardrails. This is an AI safety feature, not a key security feature. Every feature added to the proxy is code that handles sensitive data. | Forward and forget. The proxy should touch the request as little as possible. |
| **SSO / SAML / team auth** | Enterprise auth is a 6-month rabbit hole. Worthless is for solo devs and small teams first. | Single-user local proxy. Team features are future scope after hosted proxy exists. |
| **Multi-model aggregation** | Don't become "yet another LLM gateway." The market has Portkey, Helicone, LiteLLM, OpenRouter. That war is fought and won. | Be the security layer that sits in front of OR behind any gateway. Composable, not competitive. |
| **Automatic key rotation** | Infisical and Vault do this well. Rotation requires provider API integration (creating new keys via OpenAI API, etc.). Scope creep. | Provide `worthless re-enroll` to re-split a new key. Manual rotation with a good UX. |
| **Anomaly detection / alerting** | Requires ML, historical data, and a monitoring system. Massive scope for marginal security value in a local proxy. | Log request counts and spend. Let users set hard caps. Detection is future scope for hosted version. |

## Feature Dependencies

```
CLI enrollment (enroll) --> Split-key storage --> Local proxy --> Request forwarding
                                                      |
                                                      v
CLI wrapping (wrap) --------------------------------> Env var rewriting
                                                      |
                                                      v
                                              Provider support (OpenAI, Anthropic)
                                                      |
                                                      v
                                              SSE streaming support
                                                      |
                                                      v
                                              Spend tracking (local SQLite)
                                                      |
                                                      v
                                              Hard spend caps (gate-before-reconstruct)

Key: A --> B means "B depends on A"
```

Specific dependency chain:

1. **XOR split-key implementation** - Foundation. Everything else depends on this being correct.
2. **CLI enrollment (`worthless enroll`)** - Requires split-key. Produces Shard A (client file) + Shard B (server store).
3. **Local proxy server** - Requires shard storage to exist. Reconstructs key per-request.
4. **Provider routing** - Requires proxy. Must know which upstream to hit based on the enrolled key's provider.
5. **CLI wrapping (`worthless wrap`)** - Requires proxy to be runnable. Sets env vars to redirect SDK calls.
6. **Streaming support** - Requires proxy with request forwarding. SSE passthrough for `stream: true`.
7. **Spend tracking** - Requires proxy with request forwarding. Counts tokens/cost per response.
8. **Hard spend caps** - Requires spend tracking + gate-before-reconstruct architecture. Policy check before XOR.

## MVP Recommendation

**Prioritize (Phase 1 -- PoC):**

1. **XOR split-key + CLI enrollment** - This is the product. Without it, Worthless is just another proxy.
2. **Local proxy with transparent forwarding** - Users must be able to make API calls through the proxy with zero code changes.
3. **CLI wrap for env var rewriting** - The "90 seconds to working" experience depends on this.
4. **OpenAI + Anthropic provider support** - Two providers covers the critical mass.
5. **SSE streaming passthrough** - Non-negotiable. Chat completions without streaming is broken.

**Prioritize (Phase 2 -- Harden):**

6. **Local spend tracking** (SQLite) - Know what's being spent.
7. **Hard spend caps** (gate-before-reconstruct) - The second differentiator after split-key.
8. **Rust reconstruction module** - Move the XOR + key handling to memory-safe code.

**Defer:**

- **Honeypot mode** - Architecturally enabled but not user-facing priority. Phase 3+.
- **Additional providers** (Gemini, etc.) - After core two work flawlessly.
- **Hosted proxy** - After local proxy is battle-tested. Different product surface entirely.
- **MCP server integration** - Nice for Claude Code / Cursor users but not core security value.
- **Dashboard** - Terminal-first philosophy. Only if user demand proves overwhelming.

## Competitive Landscape Summary

| Capability | Portkey | Helicone | LiteLLM | Infisical | Vault | **Worthless** |
|------------|---------|----------|---------|-----------|-------|---------------|
| Key storage model | Virtual keys (stores real key) | Vault (encrypted at rest) | Virtual keys (stores real key) | Encrypted secrets | Transit encryption | **Split-key (no complete key exists)** |
| Spend caps | Enterprise only | Rate limits by cost | Hierarchical budgets | N/A | N/A | **Hard caps, gate-before-reconstruct** |
| Setup time | Minutes (cloud) | Minutes (cloud) | Heavy (Docker+Postgres for self-host) | Minutes (cloud) | Complex (Vault cluster) | **90 seconds (local CLI)** |
| Cloud dependency | Required | Optional (self-host available) | Optional | Required | Optional | **None (local-first)** |
| Language support | SDK required | SDK or proxy header | SDK or proxy | SDK/CLI | SDK/CLI | **Any (env var rewriting)** |
| Primary value | Observability + routing | Observability + caching | Multi-provider routing | Secrets management | Secrets + encryption | **Key elimination** |

The key insight: every competitor **protects** the key. Worthless **eliminates** it. This is genuinely unoccupied territory.

## Sources

- [Helicone: Top 5 LLM Gateways 2025](https://www.helicone.ai/blog/top-llm-gateways-comparison-2025)
- [Portkey: Budget Limits Docs](https://portkey.ai/docs/product/ai-gateway/virtual-keys/budget-limits)
- [Portkey: Virtual Keys](https://portkey.ai/docs/product/ai-gateway/virtual-keys)
- [LiteLLM: Virtual Keys](https://docs.litellm.ai/docs/proxy/virtual_keys)
- [LiteLLM: Budgets & Rate Limits](https://docs.litellm.ai/docs/proxy/users)
- [LiteLLM: Spend Tracking](https://docs.litellm.ai/docs/proxy/cost_tracking)
- [Helicone: Key Vault](https://docs.helicone.ai/features/advanced-usage/vault)
- [Helicone: Custom Rate Limits](https://docs.helicone.ai/features/advanced-usage/custom-rate-limits)
- [Infisical: Secrets Management](https://infisical.com/docs/documentation/platform/secrets-mgmt/overview)
- [HashiCorp Vault: Transit Engine](https://developer.hashicorp.com/vault/docs/secrets/transit)
- [Keyxor: XOR Secret Sharing](https://github.com/shazow/keyxor)
- [The Register: $82K Gemini API Key Theft](https://www.theregister.com/2026/03/03/gemini_api_key_82314_dollar_charge/)
- [Truffle Security: Google API Keys + Gemini](https://trufflesecurity.com/blog/google-api-keys-werent-secrets-but-then-gemini-changed-the-rules)
- [Sysdig: LLMjacking Threat Report](https://www.novaedgedigitallabs.tech/Blog/llmjacking-100k-ai-attack-draining-budgets)
- [DigitalAPI: 11 Best API Key Management Tools 2026](https://www.digitalapi.ai/blogs/top-api-key-management-tools)
