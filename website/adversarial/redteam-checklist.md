# Red-Team Checklist

Date: 2026-03-30
Mode: manual adversarial validation against the current Python PoC

## Use

Run these scenarios before claiming that Worthless enforces a hard budget or
that it is safe for team or public deployment.

For each scenario capture:

- setup
- observed result
- expected secure result
- whether the issue is accepted risk, bug, or doc mismatch

## Scenarios

### 1. Exposed origin plus forged forwarding header

Goal: verify whether the proxy can be turned into an authenticated spend tunnel.

Check:

- Reach the proxy origin directly.
- Send a provider path without explicit alias.
- Omit `x-worthless-shard-a`.
- Set `X-Forwarded-Proto: https`.

Secure result:

- Request is rejected before any alias inference or shard loading can help.

### 2. Single-key provider inference

Goal: test whether "one enrolled key" collapses auth for that provider path.

Check:

- Enroll exactly one OpenAI alias.
- Send a request to `/v1/chat/completions` with no alias.

Secure result:

- Request is still rejected unless explicit client auth material is present.

### 3. OpenAI streaming with no usage chunk

Goal: check whether spend can be consumed with zero or low recorded usage.

Check:

- Stream a long response.
- Do not request usage-returning stream options.
- Interrupt the stream before any terminal usage arrives.

Secure result:

- Spend reservation or fallback metering still records meaningful usage.

### 4. Anthropic non-streaming accounting

Goal: verify whether non-streaming Anthropic usage is counted correctly.

Check:

- Send a normal `/v1/messages` request with large input and output.
- Compare provider usage to `spend_log`.

Secure result:

- Recorded spend matches provider-reported usage, including input.

### 5. Anthropic streaming input-token blind spot

Goal: test whether large prompt costs disappear from metering.

Check:

- Send a streaming request with a very large prompt and modest output.
- Compare provider usage to `spend_log`.

Secure result:

- Input and output costs are both captured.

### 6. Concurrent cap bypass

Goal: verify whether parallel requests can overshoot the configured budget.

Check:

- Set a small cap.
- Launch multiple expensive requests concurrently.
- Compare allowed requests to post-hoc recorded spend.

Secure result:

- Reservations or equivalent controls prevent overshoot beyond a small bounded error.

### 7. Chunked request-body pressure

Goal: test whether body limits only work when `Content-Length` is honest.

Check:

- Send a large request body without `Content-Length`.
- Send a slow chunked request.

Secure result:

- Request is rejected or cut off before unbounded in-memory buffering.

### 8. Long-stream memory and pool exhaustion

Goal: test proxy survivability under large or many streams.

Check:

- Open many large streaming responses.
- Track memory growth, active upstream connections, and latency to other callers.

Secure result:

- Streams are bounded, memory remains controlled, and other requests degrade gracefully.

### 9. Same-user local compromise

Goal: test the real boundary on developer machines.

Check:

- Run untrusted code as the same user who runs Worthless.
- Attempt to read `~/.worthless/fernet.key`, the DB, and `shard_a`.

Secure result:

- If this succeeds, treat it as expected current boundary and document it clearly.

### 10. Ambient proxy and CA poisoning

Goal: verify whether outbound provider calls inherit dangerous environment settings.

Check:

- Set `HTTP_PROXY`, `HTTPS_PROXY`, or custom CA variables.
- Launch Worthless and observe upstream routing and trust decisions.

Secure result:

- Upstream transport ignores untrusted ambient env configuration.

### 11. Bootstrap compromise drill

Goal: test organizational resilience to installer and package compromise.

Check:

- Review whether production or team rollout depends on `curl | sh` or `npx -y`.
- Simulate a revoked package or malicious release.

Secure result:

- Teams can deploy from pinned, verified artifacts without trusting live bootstrap endpoints.

### 12. Secret retention after deletion

Goal: understand how much data remains after unenroll or cleanup.

Check:

- Enroll, use, and delete aliases.
- Inspect DB, WAL, backups, and temp artifacts.

Secure result:

- Secret-retention behavior is known, acceptable, and documented.

## Exit Criteria

Do not ship stronger product claims until these are true:

1. Exposed-origin auth collapse is closed.
2. Provider accounting is correct enough to defend a spend claim.
3. Stream and body resource limits are enforced.
4. Same-user compromise is documented honestly.
5. Install paths are no longer the easiest path to total compromise.
