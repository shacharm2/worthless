"""Live end-to-end tests: lock -> wrap -> real LLM call -> unlock.

These tests hit real provider APIs and cost real money (~$0.001 per run).
They are skipped unless the relevant API key is present in the environment.

Run:
    uv run pytest tests/test_e2e_live.py -m live -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app
from worthless.cli.bootstrap import WorthlessHome

runner = CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Child scripts — one per provider (request/response shapes differ)
# ---------------------------------------------------------------------------

_OPENAI_CHILD = textwrap.dedent("""\
    import os, sys, json, httpx
    base = os.environ.get("OPENAI_BASE_URL")
    if not base:
        print("OPENAI_BASE_URL not set", file=sys.stderr)
        sys.exit(1)
    # Disable auto-decompression — proxy may relay content-encoding
    # headers after already decompressing the upstream response.
    client = httpx.Client(headers={"accept-encoding": "identity"})
    r = client.post(
        f"{base}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "say hi"}],
        },
        timeout=60.0,
    )
    print(json.dumps({"status": r.status_code, "body": r.json()}))
""")

_ANTHROPIC_CHILD = textwrap.dedent("""\
    import os, sys, json, httpx
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if not base:
        print("ANTHROPIC_BASE_URL not set", file=sys.stderr)
        sys.exit(1)
    client = httpx.Client(headers={"accept-encoding": "identity"})
    r = client.post(
        f"{base}/v1/messages",
        json={
            "model": "claude-3-5-haiku-20241022",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "say hi"}],
        },
        headers={"anthropic-version": "2023-06-01"},
        timeout=60.0,
    )
    print(json.dumps({"status": r.status_code, "body": r.json()}))
""")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def openai_env(tmp_path: Path) -> tuple[Path, Path, str, dict[str, str]]:
    """Isolated env with a real OpenAI key. Skips if not available."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    worthless_home = tmp_path / ".worthless"

    env_file = project_dir / ".env"
    env_file.write_text(f"OPENAI_API_KEY={key}\n")

    cli_env = {"WORTHLESS_HOME": str(worthless_home)}
    return env_file, worthless_home, key, cli_env


@pytest.fixture()
def anthropic_env(tmp_path: Path) -> tuple[Path, Path, str, dict[str, str]]:
    """Isolated env with a real Anthropic key. Skips if not available."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    worthless_home = tmp_path / ".worthless"

    env_file = project_dir / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={key}\n")

    cli_env = {"WORTHLESS_HOME": str(worthless_home)}
    return env_file, worthless_home, key, cli_env


# ---------------------------------------------------------------------------
# OpenAI live test
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.timeout(120)
class TestOpenAILive:
    """Lock a real OpenAI key, send a request through the proxy, verify response."""

    def test_openai_roundtrip(self, openai_env: tuple[Path, Path, str, dict[str, str]]) -> None:
        env_file, worthless_home, original_key, cli_env = openai_env
        original_content = env_file.read_text()

        # Lock
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"lock failed: {result.output}"
        assert original_key not in env_file.read_text()

        # Wrap + real LLM call
        proc = subprocess.run(
            [
                str(Path(sys.executable).parent / "worthless"),
                "wrap",
                "--",
                sys.executable,
                "-c",
                _OPENAI_CHILD,
            ],
            env={**os.environ, "WORTHLESS_HOME": str(worthless_home)},
            timeout=90,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"wrap failed (rc={proc.returncode}):\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        # Parse child output
        data = json.loads(proc.stdout.strip())

        if data["status"] == 429:
            # Quota exhausted — but 429 from upstream still proves full
            # proxy transit: key reconstructed, request forwarded, error
            # sanitized and relayed back. Accept as a pass.
            pass
        else:
            assert data["status"] == 200, f"upstream returned {data['status']}: {data['body']}"
            body = data["body"]
            assert "choices" in body, f"missing choices in response: {body}"
            content = body["choices"][0]["message"]["content"]
            assert content, f"empty completion content: {body}"

        # Unlock
        result = runner.invoke(app, ["unlock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"unlock failed: {result.output}"
        assert env_file.read_text() == original_content

        # Clean state
        home = WorthlessHome(base_dir=worthless_home)
        assert list(home.shard_a_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Anthropic live test
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.timeout(120)
class TestAnthropicLive:
    """Lock a real Anthropic key, send a request through the proxy, verify response."""

    def test_anthropic_roundtrip(
        self, anthropic_env: tuple[Path, Path, str, dict[str, str]]
    ) -> None:
        env_file, worthless_home, original_key, cli_env = anthropic_env
        original_content = env_file.read_text()

        # Lock
        result = runner.invoke(app, ["lock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"lock failed: {result.output}"
        assert original_key not in env_file.read_text()

        # Wrap + real LLM call
        proc = subprocess.run(
            [
                str(Path(sys.executable).parent / "worthless"),
                "wrap",
                "--",
                sys.executable,
                "-c",
                _ANTHROPIC_CHILD,
            ],
            env={**os.environ, "WORTHLESS_HOME": str(worthless_home)},
            timeout=90,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"wrap failed (rc={proc.returncode}):\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
        )

        # Parse child output
        data = json.loads(proc.stdout.strip())

        if data["status"] == 429:
            # Rate-limited — but 429 from upstream still proves full
            # proxy transit: key reconstructed, request forwarded, error
            # sanitized and relayed back. Accept as a pass.
            pass
        elif data["status"] == 529:
            # Anthropic overloaded — same reasoning as 429.
            pass
        else:
            assert data["status"] == 200, f"upstream returned {data['status']}: {data['body']}"
            body = data["body"]
            assert "content" in body, f"missing content in response: {body}"
            text = body["content"][0]["text"]
            assert text, f"empty completion text: {body}"

        # Unlock
        result = runner.invoke(app, ["unlock", "--env", str(env_file)], env=cli_env)
        assert result.exit_code == 0, f"unlock failed: {result.output}"
        assert env_file.read_text() == original_content

        # Clean state
        home = WorthlessHome(base_dir=worthless_home)
        assert list(home.shard_a_dir.iterdir()) == []
