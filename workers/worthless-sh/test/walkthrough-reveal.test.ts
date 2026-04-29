import { SELF } from "cloudflare:test";
import { describe, it, expect } from "vitest";
import { WALKTHROUGH_B64 } from "../src/embedded";

// WOR-323 — walkthrough REVEAL block contract.
//
// The walkthrough served at `?explain=1` is the curl-readable audit page.
// This file pins three invariants:
//
//   1. Served body byte-equals the bundled WALKTHROUGH_B64 source — catches
//      Worker-side mutation between embedded.ts and the response.
//   2. Body starts with the exact REVEAL block at offset 0 — catches a
//      regression that drops, reorders, or double-prepends the block.
//   3. ETag matches `W/"walkthrough-${sha256(body)}"` — catches cache
//      desync between the served body and the advertised hash.
//
// Honest framing (matches DEPLOY.md "Known residual risks"): these checks
// catch transit tampering and bundle drift. They do NOT defend against an
// attacker who controls the origin — that's WOR-303 (cosign).

const CURL_UA = "curl/8.4.0";

const REVEAL_BLOCK = `== Verify the bytes match what the Worker serves ==
  curl -sSL worthless.sh | sha256sum         # body hash
  curl -sSI worthless.sh | grep -i sha256    # header value
The two MUST match. Mismatch = abort and report.
(This catches transit tampering. Not origin compromise — see README.)
==================================================================
`;

async function sha256Hex(input: string): Promise<string> {
  const buf = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(input),
  );
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// `atob` returns a binary string (each char = one byte 0..255), not a UTF-8
// decoded string. Walkthrough/install bytes contain em-dashes and other
// multi-byte UTF-8 — round-tripping via TextDecoder restores them.
function b64ToUtf8(b64: string): string {
  const bin = atob(b64);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder("utf-8").decode(bytes);
}

describe("walkthrough REVEAL block (WOR-323)", () => {
  it("served ?explain=1 body byte-equals decoded WALKTHROUGH_B64", async () => {
    const res = await SELF.fetch("https://worthless.sh/?explain=1", {
      headers: { "user-agent": CURL_UA },
    });
    expect(res.status).toBe(200);
    const body = await res.text();
    const expected = b64ToUtf8(WALKTHROUGH_B64);
    expect(body).toBe(expected);
  });

  it("served walkthrough body starts with the REVEAL block at offset 0", async () => {
    const res = await SELF.fetch("https://worthless.sh/?explain=1", {
      headers: { "user-agent": CURL_UA },
    });
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body.startsWith(REVEAL_BLOCK)).toBe(true);
    expect(body.indexOf("== Verify the bytes match what the Worker serves ==")).toBe(0);
  });

  it("ETag header equals W/\"walkthrough-${sha256(body)}\"", async () => {
    const res = await SELF.fetch("https://worthless.sh/?explain=1", {
      headers: { "user-agent": CURL_UA },
    });
    expect(res.status).toBe(200);
    const body = await res.text();
    const sha = await sha256Hex(body);
    expect(res.headers.get("etag")).toBe(`W/"walkthrough-${sha}"`);
  });
});
