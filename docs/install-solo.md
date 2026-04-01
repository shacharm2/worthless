# Install — Solo Developer

```bash
curl worthless.sh | sh
```

This will:
1. Open a browser for a one-click auth (GitHub or Google)
2. Ask you to paste your API key
3. Ask for a daily spend cap
4. Start a local proxy on `localhost:8787`

Then swap one environment variable:

```bash
export OPENAI_BASE_URL=http://localhost:8787/v1
# or
export ANTHROPIC_BASE_URL=http://localhost:8787/v1
```

Your existing code works identically. Your key now has a hard cap.

**Target: working in 90 seconds.**
