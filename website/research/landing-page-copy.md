# Worthless â Landing Page Copy

---

## 1. Hero

### Headline
**Your API keys are one leak away from a five-figure bill.**

### Subheadline
Worthless splits your LLM API keys so neither half works alone â then enforces hard spend caps before the key ever reconstructs. If the budget is blown, the key never forms. The request never fires. The bill never comes.

### CTA
`worthless enroll` â 90 seconds to protect your first key.

[Get Started](https://github.com/...) | [Read the Docs](#)

---

## 2. Problem Statement

### API keys are liability, not security

Every `.env` file is a loaded gun. One leaked key â a careless commit, a compromised CI runner, a rogue dependency â and someone else is running GPT-4 on your credit card.

Traditional secrets managers solve storage. They don't solve *what happens after the key is used*.

**The real threats:**

- **Key theft.** A plaintext API key in the wrong hands means unlimited spend until you notice and rotate. Average detection time? Days.
- **Runaway costs.** An agent stuck in a loop, a misconfigured batch job, a teammate's experiment left running overnight. No provider gives you a hard kill switch.
- **Agent autonomy.** You gave your AI agent an API key so it could call Claude. You didn't give it permission to burn $400 in a retry loop at 3 AM. But the key doesn't know that.

The key is the credential *and* the authorization *and* the budget. That's too many jobs for one string.

---

## 3. How It Works

### Three steps. No key ever travels whole again.

**1. Split**
Worthless splits your API key into two shards using XOR secret sharing â on your machine, before anything touches a server. Shard A stays with you. Shard B goes to the Worthless proxy. Neither shard reveals anything on its own. Steal one, steal both â without the other half, it's worthless.

**2. Proxy**
Every API request goes through the Worthless proxy. The proxy doesn't have your key. It has Shard B and a set of rules you defined: spend caps, rate limits, allowed models, time windows.

**3. Enforce**
Before the key ever reconstructs, the rules engine evaluates the request. Over budget? Key never forms. Wrong model? Key never forms. Rate limit hit? Key never forms. Only requests that pass every check trigger reconstruction â and the reconstructed key calls the provider directly from the server. It never comes back to you, never transits the network.

**The result:** A stolen key is worthless. A runaway agent hits a wall. Your spend has a hard ceiling, not a suggestion.

---

## 4. Features

### Split-key architecture
XOR secret sharing splits keys client-side. The server never sees the full key. Neither shard is useful alone.

### Hard spend caps
Not soft limits. Not alerts-after-the-fact. The key physically cannot reconstruct once the budget is exhausted.

### Rules engine
Spend caps, rate limits, model allowlists, token budgets, time windows. All evaluated before reconstruction â not after.

### 90-second setup
`worthless enroll` walks you through it. One command, one key, done. Your existing code doesn't change â Worthless sits in front of the provider.

### Agent-native
Ships with an MCP server for Claude Code, Cursor, and Windsurf. Agents discover capabilities automatically. Human-installed, agent-operated.

### Self-hosted
Your infrastructure, your keys, your rules. Docker Compose, Railway, Render, or Helm. No SaaS dependency required.

### Pre-commit scanning
`worthless scan` catches leaked keys before they reach your repo. Add it as a pre-commit hook and stop playing whack-a-mole with git history rewrites.

### Anomaly detection
Spend velocity monitoring flags unusual patterns before they become incidents. Get alerts via email or Slack.

---

## 5. Comparison

### Before Worthless

| | Traditional Key Management | With Worthless |
|---|---|---|
| **Key storage** | Encrypted at rest in a vault | Split into two useless halves |
| **Key in memory** | Plaintext during every request | Reconstructed server-side, never returned |
| **Spend control** | Provider billing alerts (after the fact) | Hard caps enforced before reconstruction |
| **Stolen key** | Full access until manual rotation | Worthless â literally |
| **Agent guardrails** | None â the key is the key | Rate limits, model restrictions, budgets |
| **Rotation** | Manual, disruptive | Re-enroll in seconds, no code changes |
| **Cost of failure** | Unbounded | Capped by design |

### The shift

Secrets managers protect keys **at rest**. Worthless protects keys **in use**. They're complementary â use both. But only one of them helps when the key is already out there.

---

## 6. Use Cases

### Solo developer
You're building with Claude or GPT-4. You store your key in `.env` and hope for the best. Worthless gives you a hard spend cap and pre-commit scanning in 90 seconds. Sleep better.

### Team
Five developers, three API keys, zero visibility into who's spending what. Worthless gives each team member scoped access with individual budgets, model restrictions, and rate limits. Deploy with Docker Compose in 5 minutes.

### CI/CD pipelines
Your CI runner has an API key for integration tests. If that runner is compromised, the key works everywhere. With Worthless, the CI key has a $5 budget and expires after the pipeline window. Breach contained by design.

### Agent frameworks
You're building autonomous agents that make API calls. The agent doesn't need a $10,000 credit line â it needs $2 per task with a rate limit. Worthless enforces that at the infrastructure level, not the prompt level.

### Open source maintainers
You offer a hosted demo or playground. Without Worthless, one abusive user can drain your budget. With Worthless, each session gets a token budget and a time window. Abuse hits the cap, not your wallet.

---

## 7. FAQ

**Does Worthless slow down my API calls?**
The proxy adds single-digit millisecond overhead. The rules engine evaluates before reconstruction, not during streaming. For most workloads, the latency is imperceptible.

**Do I need to change my application code?**
No. Point your base URL at the Worthless proxy instead of the provider. Your SDK, your prompts, your streaming â all unchanged.

**What providers are supported?**
OpenAI and Anthropic at launch. Gemini is a stretch goal for v1.

**Is the key ever fully assembled?**
Yes â briefly, in an isolated reconstruction service, for the duration of the upstream API call. The reconstructed key calls the provider directly and is zeroed from memory immediately after. It never returns to the proxy, never transits the network, and never reaches your application.

**What happens if the Worthless proxy goes down?**
Your requests fail, same as if the provider were down. This is a feature: the key cannot be used without the proxy enforcing your rules. No bypass path means no exploit path.

**Can I use this with my existing secrets manager?**
Yes. Worthless is complementary. Store Shard A in Vault, 1Password, or whatever you use today. Worthless protects the key in use; your vault protects it at rest.

**Is this actually secure, or security theater?**
XOR secret sharing is information-theoretically secure â one shard reveals zero bits about the key. The gate-before-reconstruct architecture means a denied request never touches key material. The reconstruction service runs in an isolated container with memory zeroing. We publish our security model and invite review.

**What does it cost?**
The open-source self-hosted version is free. Managed tiers are planned for teams that want hosted infrastructure and additional features.

---

## 8. Final CTA

### Your API key shouldn't be worth stealing.

Worthless takes 90 seconds to set up and zero lines of code to integrate. Split your first key, set a spend cap, and stop treating API keys like they're precious.

They shouldn't be.

```bash
pip install worthless
worthless enroll
```

[Get Started on GitHub](https://github.com/...) | [Read the Documentation](#) | [Join the Community](#)

---

*Open source. Self-hosted. Because your keys are your problem â and now they're not a problem at all.*
