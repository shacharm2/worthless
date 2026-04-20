# New ticket brief — Cosign-sign GHCR images + document verification

**Proposed project**: v1.1 (post-launch hardening — creates precedent for v1.1.x patch line)
**Proposed milestone**: new "Post-launch hardening" OR "(no milestone)" if user prefers flat structure
**Proposed labels**: `v1.1`, `DevOps`, `security`
**Proposed priority**: P1 (not launch-blocking; ship in the first post-launch patch)
**Proposed parent epic**: WOR-234 Launch Blockers if user wants it gated to launch line, otherwise no parent

## Story (ELI5)

We publish a Docker image to GHCR. Today, nothing cryptographically proves that image came from our CI — an attacker with GHCR write access could overwrite a tag and users wouldn't know. Cosign signs the image at publish-time with an ephemeral OIDC-bound key, and users can run one command to verify. Low effort, high trust. Security reviewer flagged this as "the one non-goal I'd push back on hardest" for WOR-236; we deferred it to keep WOR-236 shippable.

## Why this issue exists

WOR-236 ships a multi-arch image to `ghcr.io/<owner>/worthless-proxy` with SLSA provenance + SBOM attached, but **no cosign signature**. Provenance alone tells users *how* it was built; it does not cryptographically bind the artifact to this specific repo + workflow. For a tool that sits in the API-key path (users mount OpenAI/Anthropic keys into the container), unsigned releases are a posture gap.

Security reviewer verdict during WOR-236 review: "accept-as-risk for v1.1, file ticket for v1.1.1, note unsigned in release notes."

## What needs to be done

1. **Sign images in the publish workflow.**
   - Add `sigstore/cosign-installer@<sha>` step to `.github/workflows/publish-docker.yml` after `build-push`.
   - Run `cosign sign --yes ghcr.io/<owner>/worthless-proxy@${{ steps.build.outputs.digest }}` — keyless, OIDC-backed. No secrets.
   - Requires `id-token: write` permission on the `build-push` job (adds to existing `packages: write`).

2. **Document verification for users.**
   - Add to README and/or `docs/docker.md`:
     ```
     cosign verify ghcr.io/<owner>/worthless-proxy:<tag> \
       --certificate-identity-regexp 'https://github.com/<owner>/worthless/\.github/workflows/publish-docker\.yml@.*' \
       --certificate-oidc-issuer https://token.actions.githubusercontent.com
     ```
   - One paragraph explaining keyless signing, OIDC identity, why this matters.

3. **Release notes template update.**
   - Remove the "Unsigned — verify via provenance attestation" line added in v0.3.0 release notes.
   - Replace with "Signed with cosign keyless — see README for `cosign verify` command."

4. **Attestation verification in `smoke` job.**
   - Add a `cosign verify` step before the pull-and-run test, to catch any signing regressions in-CI.

## Acceptance criteria

- [ ] `cosign sign` step added to `publish-docker.yml`; permissions include `id-token: write` on build-push job.
- [ ] `cosign verify` in smoke job passes against the freshly-pushed image.
- [ ] README or `docs/docker.md` contains verified-working `cosign verify` command (tested by user on the v1.1.1 tag).
- [ ] Next `v*` tag publishes signed artifacts; manual verification from clean machine succeeds.

## Research context for the implementer

- Cosign keyless mode uses GitHub Actions OIDC — no persistent key material, no key rotation. This is the modern default; do not use `cosign generate-key-pair`.
- `--certificate-identity-regexp` must exactly match the workflow file path. Changing the workflow filename or moving it into a reusable workflow breaks the verification command — document this tradeoff.
- Transparency log (`rekor.sigstore.dev`) is public — sigstore entries are discoverable. This is fine for an OSS repo; mention in docs so users aren't surprised.
- SLSA provenance already attached in WOR-236 is **complementary**, not redundant: provenance describes the build; signature binds the artifact to the build identity.

## References

- WOR-236 spec: `.planning/ghcr-publish-spec.md`
- Sigstore docs: https://docs.sigstore.dev/cosign/signing/overview/
- GitHub Actions OIDC: https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect
- Example workflow (for patterns only): `sigstore/cosign` repo's own release workflow.

## Scope boundary

Does NOT include:
- Switching to a signed base image (tracked separately in docker-hardening research).
- Hardware keys or key-material-based signing — keyless only.
- Rekor monitoring / transparency-log alerting (v2.0+ territory).
- SLSA L3 attestations (requires hermetic builds; different ticket).

## Effort estimate

~2-3 hours. Mostly documentation — the signing step is ~8 lines of YAML.
