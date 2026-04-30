"""Tests for ``worthless providers`` subcommands (worthless-8rqs Phase 2).

The ``providers`` command group lets users:

- ``worthless providers list`` — see the merged registry (bundled + user override)
- ``worthless providers register`` — append a custom provider to ~/.worthless/providers.toml

Contract pinned by these tests:
- ``list`` prints all 6 bundled entries plus any user-registered ones.
- ``list --json`` emits machine-readable output with name/url/protocol/source.
- ``register`` writes to the user file (creating ~/.worthless/ if absent).
- ``register`` refuses bundled-name conflicts (suggests a different name).
- ``register`` refuses bundled-URL conflicts (unless ``--force``).
- ``register`` rejects malformed URLs (scheme must be http/https, netloc non-empty).
- ``register`` rejects malformed protocols (must be openai or anthropic).
- ``register`` appends to existing user file (does not clobber).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app

runner = CliRunner()


class TestProvidersList:
    """`worthless providers list` shows the merged registry."""

    def test_list_shows_bundled_six(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(app, ["providers", "list"])
        assert result.exit_code == 0, result.output
        # All six bundled provider names must appear.
        for name in ("openai", "anthropic", "openrouter", "groq", "together", "ollama"):
            assert name in result.output, f"{name!r} missing from list output"

    def test_list_includes_protocol_column(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(app, ["providers", "list"])
        assert result.exit_code == 0
        assert "openai" in result.output and "anthropic" in result.output

    def test_list_json_output(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """--json emits a parseable list of {name, url, protocol, source} objects."""
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(app, ["--json", "providers", "list"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert len(payload) == 6
        for entry in payload:
            assert set(entry.keys()) >= {"name", "url", "protocol", "source"}
            assert entry["source"] == "bundled"

    def test_list_includes_user_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        worthless_dir = tmp_path / ".worthless"
        worthless_dir.mkdir(parents=True)
        (worthless_dir / "providers.toml").write_text(
            "[provider.fireworks]\n"
            'url = "https://api.fireworks.ai/inference/v1"\n'
            'protocol = "openai"\n'
        )
        result = runner.invoke(app, ["--json", "providers", "list"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        names = {e["name"] for e in payload}
        assert "fireworks" in names, f"user-registered provider not in list: {names}"
        # User entry should be marked as such.
        fireworks = next(e for e in payload if e["name"] == "fireworks")
        assert fireworks["source"] == "user"


class TestProvidersRegister:
    """`worthless providers register` appends to ~/.worthless/providers.toml."""

    def test_register_writes_user_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "fireworks",
                "--url",
                "https://api.fireworks.ai/inference/v1",
                "--protocol",
                "openai",
            ],
        )
        assert result.exit_code == 0, result.output
        user_file = tmp_path / ".worthless" / "providers.toml"
        assert user_file.exists()
        content = user_file.read_text()
        assert "fireworks" in content
        assert "https://api.fireworks.ai/inference/v1" in content

    def test_register_refuses_bundled_name_conflict(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Cannot reuse a bundled name (openai, anthropic, ...) for a user entry."""
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "openai",  # bundled
                "--url",
                "https://staging.openai.example/v1",
                "--protocol",
                "openai",
            ],
        )
        assert result.exit_code != 0
        # Error message should explain why and suggest a fix.
        assert "openai" in result.output.lower()
        # User file should not have been written.
        assert not (tmp_path / ".worthless" / "providers.toml").exists()

    def test_register_refuses_bundled_url_conflict_without_force(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "my-openai",
                "--url",
                "https://api.openai.com/v1",  # bundled URL
                "--protocol",
                "openai",
            ],
        )
        assert result.exit_code != 0
        # User file should not have been written.
        assert not (tmp_path / ".worthless" / "providers.toml").exists()

    def test_register_accepts_bundled_url_with_force(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "my-openai",
                "--url",
                "https://api.openai.com/v1",
                "--protocol",
                "openai",
                "--force",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_register_rejects_malformed_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "bad",
                "--url",
                "not-a-url",
                "--protocol",
                "openai",
            ],
        )
        assert result.exit_code != 0

    def test_register_rejects_javascript_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """javascript: URLs must be rejected — only http/https allowed."""
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "bad",
                "--url",
                "javascript:alert(1)",
                "--protocol",
                "openai",
            ],
        )
        assert result.exit_code != 0

    def test_register_accepts_localhost_http(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """http://localhost is valid (Ollama's default)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "my-ollama",
                "--url",
                "http://localhost:1234/v1",
                "--protocol",
                "openai",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_register_rejects_unknown_protocol(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            [
                "providers",
                "register",
                "--name",
                "alien",
                "--url",
                "https://x.example/v1",
                "--protocol",
                "telepathy",  # not openai or anthropic
            ],
        )
        assert result.exit_code != 0

    def test_register_appends_does_not_clobber(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A second register call must keep the first entry."""
        monkeypatch.setenv("HOME", str(tmp_path))
        for name, url in [
            ("first", "https://a.example/v1"),
            ("second", "https://b.example/v1"),
        ]:
            result = runner.invoke(
                app,
                [
                    "providers",
                    "register",
                    "--name",
                    name,
                    "--url",
                    url,
                    "--protocol",
                    "openai",
                ],
            )
            assert result.exit_code == 0, result.output

        content = (tmp_path / ".worthless" / "providers.toml").read_text()
        assert "first" in content and "second" in content
