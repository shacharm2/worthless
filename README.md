# Worthless

**Make leaked API keys worthless.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)
[![Tests](https://github.com/shacharm2/worthless/actions/workflows/tests.yml/badge.svg)](https://github.com/shacharm2/worthless/actions/workflows/tests.yml)

<!-- TODO: Excalidraw hero SVG — assets/hero.svg -->

Your API key is split. Each piece is useless on its own.

If the budget is blown, the key never forms at all.

> Every secrets tool protects the key until your app uses it.
> Worthless protects you **after it leaks**.

## Get started

```bash
pip install worthless
```

Lock your keys:

```console
$ worthless lock
1 key(s) protected.
```

Run your app:

```console
$ worthless wrap python app.py
```

That's it. Your code doesn't change.

## Undo everything

```console
$ worthless unlock
1 key(s) restored.
```

Original key is back. No trace.

## Learn more

- [How it works](docs/ARCHITECTURE.md) — technical deep dive
- [Security rules](SECURITY_RULES.md) — invariants all contributions must preserve
- [Wire protocol](docs/PROTOCOL.md) — headers, endpoints, error codes

## Pre-commit hook

```yaml
repos:
  - repo: https://github.com/shacharm2/worthless
    rev: main
    hooks:
      - id: worthless-scan
```

## Development

```bash
git clone https://github.com/shacharm2/worthless && cd worthless
uv sync --extra dev --extra test
uv run pytest
```

## Contributing

PRs welcome. Read [SECURITY_RULES.md](SECURITY_RULES.md) first.

## License

[AGPL-3.0](LICENSE)
