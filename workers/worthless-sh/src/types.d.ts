// Ambient module declarations for Wrangler Text imports.
//
// `wrangler.toml` declares `[[rules]]` of type "Text" for `*.sh` and `*.txt`,
// which makes Wrangler bundle those files as string exports at build time.
// TypeScript needs these declarations to type the `import X from "...sh"`
// expression; without them tsc would error on the `.sh` extension.
//
// The build-time inlining is the heart of the Option A architecture
// (engineering/adr/001-worthless-sh-inline-bundle.md): install.sh ships
// inside the Worker bundle, sha256 is verifiable from the response header,
// and there is no runtime fetch from GitHub (eliminating the supply-chain
// branch that fetched a mutable URL).

declare module "*.sh" {
  const content: string;
  export default content;
}

declare module "*.txt" {
  const content: string;
  export default content;
}
