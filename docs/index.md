---
title: "Worthless"
description: "Make API keys worthless to steal."
template: splash
hero:
  tagline: Two halves. Hard cap. Direct upstream call. Your leaked .env becomes worthless.
  actions:
    - text: Install (Solo Dev)
      link: ./install-solo/
      icon: right-arrow
      variant: primary
    - text: View on GitHub
      link: https://github.com/shacharm2/worthless
      icon: external
      variant: minimal
---

import { Card, CardGrid } from '@astrojs/starlight/components';

## What Worthless does

Worthless makes API keys worthless to steal through two mechanisms:

1. **Client-side splitting** — your real API key is split into two halves using XOR secret sharing. Neither half reveals anything alone.
2. **Gate before reconstruction** — every request hits the rules engine *before* the second half is fetched. Budget blown = key never forms = request never reaches the provider.

<CardGrid>
  <Card title="Solo Developer" icon="laptop">
    Install in 90 seconds with `pipx install worthless`. [Get started →](./install-solo/)
  </Card>
  <Card title="Docker" icon="seti:docker">
    Pull a signed, multi-arch image from GHCR. [Pull image →](./install-docker/)
  </Card>
  <Card title="Claude Code / Cursor / Windsurf" icon="seti:code-search">
    MCP server for editor SDK integration. [Wire it up →](./install-mcp/)
  </Card>
  <Card title="GitHub Actions" icon="github">
    Protect API keys during CI test runs. [Add to CI →](./install-github-actions/)
  </Card>
</CardGrid>

## How it works

The proxy enforces three architectural invariants:

- **Client-side splitting.** The split function runs on the client exclusively. The server only ever holds Shard B.
- **Gate before reconstruction.** Denied request = zero KMS calls, zero reconstruction, zero key material touched.
- **Server-side direct upstream call.** The reconstruction service calls the LLM provider directly. The reconstructed key never returns to the proxy and never transits the network back to you.

Read the [security model](./security/) and [wire protocol](./protocol/) for the full picture.
