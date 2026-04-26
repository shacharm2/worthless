// (No ambient module declarations needed.)
//
// install.sh and walkthrough.txt are no longer imported as wildcard
// modules. They're base64-encoded at build time by
// `scripts/embed-assets.mjs` into `src/embedded.ts`, which is a normal
// .ts file and needs no ambient declaration. See wrangler.toml for the
// CF WAF rationale.

export {};
