# Provider API Key Format Research

**Date**: 2026-03-30
**Purpose**: Document exact key formats for decoy generation (WOR-31)

---

## Summary Table

| Provider | Prefix | Random Charset | Total Length | Internal Structure | Checksum? |
|----------|--------|---------------|-------------|-------------------|-----------|
| OpenAI | `sk-proj-` | `[A-Za-z0-9_-]` (base64url) | ~164 chars | Two random segments flanking `T3BlbkFJ` marker | No checksum, but has embedded marker |
| Anthropic | `sk-ant-api03-` | `[a-zA-Z0-9_-]` (base64url) | 108 chars | 93 random chars after prefix, ends with `AA` | No checksum, but has `AA` suffix |
| Google/Gemini | `AIzaSy` | `[A-Za-z0-9_-]` (base64url) | 39 chars | No segments; flat random after prefix | No |
| xAI/Grok | `xai-` | `[a-zA-Z0-9]` (alphanumeric) | ~84 chars (estimated) | No known segments; flat random after prefix | No |

---

## Detailed Findings

### 1. OpenAI (`sk-proj-`)

**Prefix**: `sk-proj-` (8 chars)

**Format Evolution**:
- Pre-Sept 2024: Total length ~56 chars (prefix + 48 random)
- Post-Sept 2024: Total length ~164 chars (current format)

**Internal Structure** (critical finding):
The key contains an embedded base64 marker `T3BlbkFJ` which decodes to `"OpenAI"` in ASCII. This marker sits in the middle of the key, splitting the random portion into two segments.

**Gitleaks regex** (authoritative source):
```
sk-(?:proj|svcacct|admin)-(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})T3BlbkFJ(?:[A-Za-z0-9_-]{74}|[A-Za-z0-9_-]{58})
```

**Structure breakdown**:
```
sk-proj-[A-Za-z0-9_-]{~74}T3BlbkFJ[A-Za-z0-9_-]{~74}
|prefix |---segment 1----|--marker--|---segment 2----|
```

**Character set**: Base64url — uppercase, lowercase, digits, underscore, hyphen.

**Checksum**: None known. No client-side validation beyond prefix+marker format matching. Keys are validated server-side only.

**Decoy implications**: MUST include the `T3BlbkFJ` marker at the correct position. Without it, the key fails format checks by secret scanners and likely by OpenAI's own routing layer. Total length should be ~164 chars.

**Example** (from gitleaks test vectors):
```
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
```

---

### 2. Anthropic (`sk-ant-api03-`)

**Prefix**: `sk-ant-api03-` (13 chars)

**Prefix breakdown**:
- `sk` = secret key
- `ant` = Anthropic
- `api03` = API version/generation

**Gitleaks regex** (authoritative source):
```
sk-ant-api03-[a-zA-Z0-9_\-]{93}AA
```

**Structure breakdown**:
```
sk-ant-api03-[a-zA-Z0-9_-]{93}AA
|--prefix----|---random body---|sf|
```

**Total length**: 108 chars (13 prefix + 93 random + 2 suffix `AA`)

**Character set**: Base64url — uppercase, lowercase, digits, underscore, hyphen.

**Suffix**: Keys consistently end with `AA`. This is likely a base64 padding artifact from the underlying binary key material (two base64 `A` chars = zero-padding of the last few bits).

**Checksum**: No known checksum. The `AA` suffix is structural (padding), not a computed check value.

**Decoy implications**: Must be exactly 108 chars total. Must end with `AA`. Random portion uses base64url charset. The `AA` suffix is trivial to replicate.

---

### 3. Google AI / Gemini (`AIzaSy`)

**Prefix**: `AIzaSy` (6 chars)

**Total length**: 39 chars (6 prefix + 33 random)

**Gitleaks/Microsoft regex**:
```
AIzaSy[A-Za-z0-9_\-]{33}
```

**Character set**: Base64url — uppercase, lowercase, digits, underscore, hyphen. Google describes these as "Base64 encoded 210-bit symmetric key."

**Internal structure**: None. Flat random string after the prefix. No segments, no separators, no embedded markers.

**Checksum**: No known checksum or validation digit. Microsoft's Purview SIT definition marks checksum as "No" for Google API keys. Validation is server-side only.

**Important note**: Google API keys were historically considered low-sensitivity (they only identify a project, not authorize access). With Gemini, they now grant model access, making them security-critical. The format has NOT changed.

**Decoy implications**: Simplest format to replicate. Just `AIzaSy` + 33 random base64url chars = 39 chars total.

---

### 4. xAI / Grok (`xai-`)

**Prefix**: `xai-` (4 chars)

**Total length**: Not officially documented. Based on the leaked key incident (Krebs on Security, May 2025) and community reports, keys appear to be approximately 84 chars total (~80 chars of random material after prefix). This needs verification with a real key.

**Character set**: Alphanumeric (`[a-zA-Z0-9]`). No evidence of underscores or hyphens in the random portion (unlike the other three providers).

**Internal structure**: No known segments, markers, or separators. Flat random string after prefix.

**Checksum**: No known checksum. No gitleaks rule exists yet (as of early 2026), suggesting the format is simple enough that entropy-based detection is the primary approach.

**Decoy implications**: Least documented format. Recommend generating `xai-` + 80 alphanumeric chars as a reasonable approximation. Should verify against a real key's length before shipping.

---

## Key Takeaways for Decoy Generation

### Critical format requirements (will break if wrong):

1. **OpenAI**: MUST embed `T3BlbkFJ` marker at the correct position (~74 chars after prefix). Without this, the key is trivially distinguishable from real keys.
2. **Anthropic**: MUST end with `AA`. Must be exactly 108 chars total.
3. **Google**: Must be exactly 39 chars total. Prefix is `AIzaSy` (not just `AIza`).
4. **xAI**: Simplest format, but least documented. Length approximation may need tuning.

### No provider uses checksums

None of the four providers implement client-side checksum validation in their key format. All validation is server-side (hit the API, get 401). This is favorable for decoy generation -- a well-formatted fake key is indistinguishable from a real key without making an API call.

### Character set summary

| Provider | Charset | Notes |
|----------|---------|-------|
| OpenAI | `[A-Za-z0-9_-]` | Base64url |
| Anthropic | `[A-Za-z0-9_-]` | Base64url |
| Google | `[A-Za-z0-9_-]` | Base64url (210-bit key) |
| xAI | `[A-Za-z0-9]` | Plain alphanumeric (no _ or -) |

### Existing codebase patterns

The repo already has provider prefix detection in `src/worthless/cli/key_patterns.py`. The `PROVIDER_PREFIXES` dict and `detect_provider()` function should be updated to reflect:
- Google prefix should be `AIzaSy` (6 chars) not just `AIza` (4 chars) for tighter matching
- Key generation needs the structural elements (OpenAI marker, Anthropic suffix) documented above

---

## Sources

- [Gitleaks PR #1780 - OpenAI regex](https://github.com/gitleaks/gitleaks/pull/1780)
- [Gitleaks config/gitleaks.toml](https://github.com/gitleaks/gitleaks/blob/master/config/gitleaks.toml)
- [OpenAI Community - Key length change](https://community.openai.com/t/project-api-key-length-has-it-changed-from-48-to-156/920777)
- [OpenAI Community - Valid characters](https://community.openai.com/t/what-are-the-valid-characters-for-the-apikey/288643)
- [GitGuardian - OpenAI Project API Key v2](https://docs.gitguardian.com/secrets-detection/secrets-detection-engine/detectors/specifics/openai_project_apikey_v2)
- [GitGuardian - Claude API Key](https://docs.gitguardian.com/secrets-detection/secrets-detection-engine/detectors/specifics/claude_api_key)
- [Microsoft Purview - Google API key SIT](https://learn.microsoft.com/en-us/purview/sit-defn-google-api-key)
- [Langfuse Issue #9070 - Anthropic key length](https://github.com/langfuse/langfuse/issues/9070)
- [n8n Issue #17761 - Anthropic key length limitation](https://github.com/n8n-io/n8n/issues/17761)
- [Krebs on Security - xAI key leak](https://krebsonsecurity.com/2025/05/xai-dev-leaks-api-key-for-private-spacex-tesla-llms/)
- [GitGuardian - xAI secret leak disclosure](https://blog.gitguardian.com/xai-secret-leak-disclosure/)
- [Glama - Designing Secure API Keys](https://glama.ai/blog/2024-10-18-what-makes-a-good-api-key)
