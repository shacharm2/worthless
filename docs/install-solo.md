# Install -- Solo Developer

[Back to wless.io](https://wless.io/)

## Standard install

```bash
curl -sSL https://worthless.sh | sh
cd your-project
worthless
```

The installer bootstraps Worthless and puts the `worthless` CLI on your PATH.
Then `worthless` scans the current project and walks you through locking keys.

What it does:

1. Scan your `.env` for API keys
2. Split each key into two shards (one local, one encrypted in the proxy DB)
3. Replace the `.env` value with a format-correct decoy
4. Start a local proxy on `localhost:8787`
5. Inject `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` so your SDK routes through the proxy

Your existing code works identically. Your key is now split and budget-protected.

## Manual fallback (from source)

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv pip install -e .
worthless
```

See the [README quickstart](../README.md#quickstart) for full walkthrough.
