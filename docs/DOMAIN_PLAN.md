# Domain Setup Plan: worthless.cloud + worthless.sh

## Overview

Two domains, two purposes, maximum security. Both on Cloudflare.

| Domain | Purpose | Backend |
|---|---|---|
| `worthless.cloud` | Marketing site (GitHub Pages) | Static HTML from `docs/` on `website` branch |
| `worthless.sh` | Install script (`curl worthless.sh \| sh`) | Cloudflare Worker |

---

## Part 1: worthless.cloud → GitHub Pages

### Step 1: GitHub Pages configuration

1. Go to **github.com/shacharm2/worthless → Settings → Pages**
2. Source: Deploy from branch → `website` branch, `/docs` folder
3. Custom domain: `worthless.cloud`
4. Check "Enforce HTTPS" (greyed out until DNS propagates — come back later)

### Step 2: Cloudflare DNS records

In Cloudflare dashboard → `worthless.cloud` → DNS:

| Type | Name | Value | Proxy |
|---|---|---|---|
| A | `@` | `185.199.108.153` | **DNS only** (grey cloud) |
| A | `@` | `185.199.109.153` | **DNS only** (grey cloud) |
| A | `@` | `185.199.110.153` | **DNS only** (grey cloud) |
| A | `@` | `185.199.111.153` | **DNS only** (grey cloud) |
| CNAME | `www` | `shacharm2.github.io` | **DNS only** (grey cloud) |

**Why grey cloud (DNS only)?** GitHub Pages provisions its own Let's Encrypt cert. Cloudflare proxy (orange cloud) creates double-TLS and can cause redirect loops or cert provisioning failures. Grey cloud = GitHub handles everything cleanly.

### Step 3: CNAME file in repo

Add a `CNAME` file to `docs/` (GitHub may auto-create this):

```
worthless.cloud
```

### Step 4: www redirect

Add a Cloudflare redirect rule (Configuration Rules):
- `www.worthless.cloud/*` → `https://worthless.cloud/$1` (301 permanent)

### Step 5: Verify

- Wait 10-30 min for DNS propagation
- Visit `https://worthless.cloud` — should show your site
- Visit `http://worthless.cloud` — should redirect to HTTPS
- Visit `https://www.worthless.cloud` — should redirect to apex

---

## Part 2: worthless.sh → Install Script (Cloudflare Worker)

### Architecture

A Cloudflare Worker at the apex serves:
- **curl/wget** → shell install script (`text/plain`)
- **browser** → HTML landing page with instructions + script source link

This is how `bun.sh`, `rustup.rs`, and `deno.land` all work.

### Step 1: Cloudflare DNS for worthless.sh

| Type | Name | Value | Proxy |
|---|---|---|---|
| A | `@` | `192.0.2.1` | **Proxied** (orange cloud) |
| AAAA | `@` | `100::` | **Proxied** (orange cloud) |

These are dummy addresses — the Worker intercepts all traffic before it reaches any origin.

### Step 2: Create Worker

In Cloudflare → Workers & Pages → Create Worker:
- Name: `worthless-install`
- Route: `worthless.sh/*`

Worker logic (simplified):
```javascript
export default {
  async fetch(request) {
    const ua = request.headers.get('user-agent') || '';
    const isCurl = /curl|wget|httpie|fetch|powershell/i.test(ua);

    if (isCurl) {
      // Serve install script
      const script = await getInstallScript(); // from KV or inline
      return new Response(script, {
        headers: {
          'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'max-age=300',
        }
      });
    }

    // Serve landing page to browsers
    return new Response(landingPageHTML, {
      headers: { 'Content-Type': 'text/html; charset=utf-8' }
    });
  }
};
```

### Step 3: Install script design

The script should be a thin wrapper:
```bash
#!/bin/sh
set -eu

main() {
    echo "Installing worthless..."

    # Detect package manager
    if command -v uv >/dev/null 2>&1; then
        uv tool install worthless
    elif command -v pipx >/dev/null 2>&1; then
        pipx install worthless
    else
        echo "Error: uv or pipx required. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi

    echo "Done! Run 'worthless enroll' to get started."
}

main
```

**Security**: The `main()` wrapper prevents partial execution if the download is truncated mid-stream.

### Step 4: Landing page

When visiting `worthless.sh` in a browser, show:
- The `curl worthless.sh | sh` command
- Link to view the script source
- Current SHA-256 checksum
- Link back to `worthless.cloud`

---

## Part 3: Security Hardening (Both Domains)

### Cloudflare-level (do for BOTH domains)

| Setting | Location | Value |
|---|---|---|
| DNSSEC | DNS → Settings | **Enable** (then add DS record if not Cloudflare registrar) |
| Always Use HTTPS | SSL/TLS → Edge Certificates | **On** |
| Min TLS Version | SSL/TLS → Edge Certificates | **1.2** |
| HSTS | SSL/TLS → Edge Certificates | **Enable** (max-age=31536000, includeSubDomains, preload) |
| Automatic HTTPS Rewrites | SSL/TLS → Edge Certificates | **On** |
| Bot Fight Mode | Security → Bots | **On** |
| Browser Integrity Check | Security → Settings | **On** |

### DNS hardening (both domains)

Add CAA records to restrict who can issue certificates:

| Type | Name | Value |
|---|---|---|
| CAA | `@` | `0 issue "letsencrypt.org"` |
| CAA | `@` | `0 issue "digicert.com"` (Cloudflare uses DigiCert) |
| CAA | `@` | `0 issuewild ";"` (block wildcard certs) |

### Email DNS records (worthless.cloud)

Even if you don't send email FROM these domains, lock them down to prevent spoofing:

| Type | Name | Value |
|---|---|---|
| TXT | `@` | `v=spf1 include:_spf.mx.cloudflare.net ~all` |
| TXT | `_dmarc` | `v=DMARC1; p=reject; rua=mailto:dmarc@worthless.cloud` |

For `worthless.sh` (no email at all):

| Type | Name | Value |
|---|---|---|
| TXT | `@` | `v=spf1 -all` |
| TXT | `_dmarc` | `v=DMARC1; p=reject` |
| MX | `@` | `0 .` (null MX — RFC 7505) |

---

## Part 4: Email Routing (worthless.cloud)

### Setup in Cloudflare

Dashboard → `worthless.cloud` → Email → Email Routing:

1. Add destination: `shachar@uglabs.io` (verify it via email link)
2. Create routes:

| Address | Forward to |
|---|---|
| `security@worthless.cloud` | `shachar@uglabs.io` |
| `hello@worthless.cloud` | `shachar@uglabs.io` |
| Catch-all | `shachar@uglabs.io` (optional — catches typos) |

### security.txt

Add `docs/.well-known/security.txt` to the website:

```
Contact: mailto:security@worthless.cloud
Expires: 2027-04-15T00:00:00.000Z
Preferred-Languages: en
Canonical: https://worthless.cloud/.well-known/security.txt
```

---

## Part 5: Discord — Not Yet

Discord is premature before public launch. It requires active moderation and sets support expectations you can't meet solo.

**Instead:**
- Enable GitHub Discussions on the repo for community Q&A
- Add `security@worthless.cloud` for vulnerability reports
- Add `hello@worthless.cloud` in the website footer
- Consider Discord after 50+ active users

---

## Execution Order (checklist)

### Phase 1: DNS + Security (do first, both domains)
- [ ] Enable DNSSEC on `worthless.cloud`
- [ ] Enable DNSSEC on `worthless.sh`
- [ ] Add CAA records on both domains
- [ ] Add SPF/DMARC records on both domains
- [ ] Enable "Always Use HTTPS" on both
- [ ] Set min TLS to 1.2 on both
- [ ] Enable HSTS on both (with preload)
- [ ] Enable Bot Fight Mode on both
- [ ] Enable Browser Integrity Check on both

### Phase 2: worthless.cloud → GitHub Pages
- [ ] Add A records (4x) + www CNAME in Cloudflare (grey cloud)
- [ ] Add `CNAME` file to `docs/` in repo
- [ ] Configure GitHub Pages (Settings → Pages → website branch, /docs)
- [ ] Wait for DNS propagation
- [ ] Enable "Enforce HTTPS" in GitHub Pages settings
- [ ] Add www→apex redirect rule in Cloudflare
- [ ] Verify site loads at `https://worthless.cloud`

### Phase 3: Email routing
- [ ] Set up Cloudflare Email Routing for worthless.cloud
- [ ] Add `security@` and `hello@` forwarding to shachar@uglabs.io
- [ ] Verify by sending test emails
- [ ] Add `security.txt` to website

### Phase 4: worthless.sh → Cloudflare Worker
- [ ] Add dummy A/AAAA records (proxied/orange cloud)
- [ ] Create and deploy Cloudflare Worker
- [ ] Write install script
- [ ] Write browser landing page
- [ ] Test: `curl worthless.sh` should return script
- [ ] Test: browser should show landing page
- [ ] Publish SHA-256 checksum in README

---

## Answers to Your Questions

**Secondary email for requests?** No — use Cloudflare Email Routing instead. Create `security@worthless.cloud` and `hello@worthless.cloud` forwarding to your uglabs email. Professional, free, and you control it from one place.

**Discord?** Not yet. GitHub Discussions first. Discord when you have users.

**Cost?** Everything above is free. Cloudflare free plan covers DNS, Workers (100k req/day), email routing, DNSSEC, DDoS protection, and basic WAF. GitHub Pages is free for public repos.
