# Install -- GitHub Actions

Run Worthless in CI to protect API keys during test runs and scan for exposed secrets.

## Workflow

Add this to `.github/workflows/worthless-ci.yml`, or copy from
[`examples/ci/worthless-ci.yml`](../examples/ci/worthless-ci.yml):

```yaml
name: Worthless CI Gate

on:
  push:
    branches: [main]
  pull_request:

jobs:
  protect-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Install worthless from source
        run: |
          git clone https://github.com/shacharm2/worthless /tmp/worthless
          cd /tmp/worthless && uv pip install -e . --system

      - name: Lock API keys
        run: worthless lock
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}

      - name: Run tests through proxy
        run: worthless wrap pytest

      - name: Scan for exposed keys
        run: worthless scan --pre-commit
```

## What this does

1. **Lock** -- Reads `OPENAI_API_KEY` from GitHub Secrets, splits it, stores shards locally in the runner
2. **Wrap** -- Starts an ephemeral proxy, injects `OPENAI_BASE_URL`, runs `pytest` through it
3. **Scan** -- Checks staged files and environment for any exposed (unprotected) keys. Exits non-zero if found.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All keys protected, scan clean |
| 1 | Unprotected keys found (scan) or key operation failed |
| 2 | Scan error (invalid config, file access error) |

> [!NOTE]
> **Planned: Hosted proxy in CI.** A hosted proxy service that eliminates the need
> to install Worthless in each CI run is planned for a future release.
> Functional CI gate testing is tracked in Phase 04.2.
