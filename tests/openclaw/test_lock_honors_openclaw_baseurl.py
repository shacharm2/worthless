"""worthless-thuu (P0) — lock must honor openclaw.json's provider baseUrl.

# The bug being closed

`worthless lock` reads the upstream URL for each provider from
`_resolve_upstream_base_url`, which only consults the user's `.env`
``*_BASE_URL`` var or falls back to the bundled provider-registry default.
It never consults ``openclaw.json``'s ``models.providers.X.baseUrl`` —
yet that field IS the source of truth for which upstream the user
actually wants OpenClaw to call.

The silent failure: an OpenClaw user whose ``openai`` provider entry
points at a non-default upstream (OpenRouter, Azure OpenAI, custom
gateway) but who does NOT set a ``*_BASE_URL`` in their .env, will have
their `worthless lock` store ``https://api.openai.com/v1`` as the proxy's
upstream. The proxy then reconstructs the (correct OpenRouter) key, sends
it to api.openai.com, and gets 401. OpenClaw renders the failure as
``[assistant turn failed before producing content]`` with no diagnostic
visible to the user. The chat is silently broken.

Discovered live during F2 G5-A chat-e2e debug session, 2026-06-07.

# What this test pins

A SecretRef-free OpenClaw provider entry whose ``baseUrl`` is an
OpenRouter URL (registered in the bundled provider registry) + a .env
that carries ONLY the API key (no ``OPENAI_BASE_URL``). After
``worthless lock`` runs, the shards row's ``base_url`` column MUST
equal the OpenClaw entry's ``baseUrl`` — not the registry default for
the "openai" wire protocol.

Today this test FAILS because `_resolve_upstream_base_url` ignores
OpenClaw entirely. The fix threads openclaw.json's baseUrl into the
lock-time upstream resolution.

# Precedence (the new contract)

1. Explicit `.env` ``*_BASE_URL`` (user-set, highest precedence) — existing.
2. NEW: ``openclaw.json``'s ``models.providers.X.baseUrl`` when OpenClaw
   is detected and the entry exists with a non-proxy URL — the OpenClaw
   user's truth about which upstream they're using.
3. Bundled registry default for the wire protocol — existing fallback.

# Out of scope (banked as siblings under worthless-thuu)

* Re-lock doesn't refresh upstream URL even when .env adds
  ``OPENAI_BASE_URL``: ``upsert_locked_shard(base_url=db_shard.base_url
  or upstream_base_url)`` at lock.py:415 preserves the existing column.
  Sibling concern, separate RED.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome
from worthless.storage.repository import ShardRepository

runner = CliRunner()


# An OpenRouter key — high-entropy (passes the scan entropy gate at 3.9),
# matches sk-or-v1- prefix so detect_provider classifies it as openrouter
# (which resolves to the openai wire protocol). Fixed-value (not generated
# from secrets.token_hex) for test byte-stability; bytes were picked once
# from a real CSPRNG and pasted here so re-runs are identical.
_OPENROUTER_KEY = "sk-or-v1-" + "68bdd68f6a982030647ad8e10ee3655122ea73e6353686aaf33fcefe0635b524"
_OPENROUTER_URL = "https://openrouter.ai/api/v1"


@pytest.fixture
def sandboxed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin HOME so detect() probes the sandbox, not the dev's real ~/.openclaw."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(home)
    return home


@pytest.fixture
def openclaw_with_openrouter(sandboxed_home: Path) -> dict[str, Path]:
    """OpenClaw config where models.providers.openai.baseUrl points at
    OpenRouter — the canonical 'I use OpenRouter through the OpenAI
    protocol' setup that this bug breaks.

    apiKey is the same plaintext key the .env will carry — that mirrors
    the real-world flow where the user has both their .env and their
    OpenClaw config pointed at the same key value.
    """
    openclaw_dir = sandboxed_home / ".openclaw"
    workspace = openclaw_dir / "workspace"
    workspace.mkdir(parents=True)
    config_path = openclaw_dir / "openclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "models": {
                    "providers": {
                        "openai": {
                            "baseUrl": _OPENROUTER_URL,
                            "apiKey": _OPENROUTER_KEY,
                            "api": "openai-completions",
                            "models": [],
                        }
                    }
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"home": sandboxed_home, "workspace": workspace, "config_path": config_path}


@pytest.fixture
def env_with_only_api_key(tmp_path: Path) -> Path:
    """.env with ONLY OPENAI_API_KEY (an OpenRouter key) — NO OPENAI_BASE_URL.

    This is the canonical 'I trust openclaw.json to know where to send
    requests; I just put the key in my .env' setup. Today this is the
    setup where the bug silently breaks chat.
    """
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={_OPENROUTER_KEY}\n")
    return env


def test_lock_uses_openclaw_baseurl_when_env_base_url_absent(
    home_dir: WorthlessHome,
    env_with_only_api_key: Path,
    openclaw_with_openrouter: dict[str, Path],
) -> None:
    """Lock against openclaw.json with `openrouter.ai` baseUrl + .env without
    `OPENAI_BASE_URL` → DB shards row's `base_url` MUST equal `openrouter.ai`.

    Today this fails: lock falls back to the registry default for the
    "openai" wire protocol (`https://api.openai.com/v1`), which is what
    the proxy then forwards to. With OpenRouter's key. Result: silent
    401, chat dies, no diagnostic.

    The fix threads openclaw.json's baseUrl into _resolve_upstream_base_url.
    """
    result = runner.invoke(
        app,
        ["lock", "--env", str(env_with_only_api_key), "--keys-only", "--allow-hardcoded-urls"],
        env={"WORTHLESS_HOME": str(home_dir.base_dir)},
    )
    assert result.exit_code == 0, (
        f"lock should succeed; failed instead:\n{result.output}\n{result.exception}"
    )

    # Find the shards row that lock created and read its upstream base_url
    repo = ShardRepository(str(home_dir.db_path), home_dir.fernet_key)
    enrollments = asyncio.run(repo.list_enrollments())
    assert enrollments, "lock should have created at least one enrollment"
    alias = enrollments[0].key_alias
    enc = asyncio.run(repo.fetch_encrypted(alias))
    assert enc is not None, f"no shards row for alias {alias}"

    assert enc.base_url == _OPENROUTER_URL, (
        f"lock stored WRONG upstream URL in DB:\n"
        f"  expected (from openclaw.json):   {_OPENROUTER_URL!r}\n"
        f"  got (registry default for openai): {enc.base_url!r}\n"
        f"This is the worthless-thuu P0 bug — proxy will now forward to\n"
        f"the wrong upstream and OpenRouter's key will return 401."
    )


def test_lock_env_base_url_still_wins_over_openclaw_baseurl(
    home_dir: WorthlessHome,
    tmp_path: Path,
    openclaw_with_openrouter: dict[str, Path],
) -> None:
    """Precedence guard: if the .env DOES carry an explicit `*_BASE_URL`,
    that wins over openclaw.json. The .env is the user's most explicit
    expression of intent — overriding it would surprise users who already
    have working multi-env setups.

    Today this passes (the env-wins path is the only path). It MUST still
    pass after the fix.
    """
    # User has set an explicit BASE_URL pointing at OpenAI (registered);
    # openclaw.json still has OpenRouter. The .env wins.
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={_OPENROUTER_KEY}\nOPENAI_BASE_URL=https://api.openai.com/v1\n")

    result = runner.invoke(
        app,
        ["lock", "--env", str(env), "--keys-only", "--allow-hardcoded-urls"],
        env={"WORTHLESS_HOME": str(home_dir.base_dir)},
    )
    assert result.exit_code == 0, result.output

    repo = ShardRepository(str(home_dir.db_path), home_dir.fernet_key)
    enrollments = asyncio.run(repo.list_enrollments())
    alias = enrollments[0].key_alias
    enc = asyncio.run(repo.fetch_encrypted(alias))
    assert enc is not None

    assert enc.base_url == "https://api.openai.com/v1", (
        f"explicit OPENAI_BASE_URL in .env should win, got {enc.base_url!r}"
    )
