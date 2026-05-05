// Ambient module declarations.
//
// install.sh and walkthrough.txt are not imported into the Worker bundle
// itself — they're base64-encoded at build time by
// `scripts/embed-assets.mjs` into `src/embedded.ts`, which is a normal
// .ts file. See wrangler.toml for the CF WAF rationale.
//
// However, `test/embed-assets-roundtrip.test.ts` (WOR-404) imports the
// same source files via Vite's `?raw` suffix to assert byte-for-byte
// equality between the on-disk source and the decoded base64 constant.
// `?raw` is a build-time read handled by Vite — content is inlined as
// a string at bundle time, so no `node:fs` at runtime.

declare module "*?raw" {
  const content: string;
  export default content;
}
