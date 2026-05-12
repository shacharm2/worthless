"""CLI integration tests for ``worthless scan --code`` (worthless-7sl9).

These exercise the typer wiring + output formatting + the AI-agent prompt
block. Pure scanner correctness lives in test_code_scanner.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from worthless.cli.app import app

runner = CliRunner(mix_stderr=False)


@pytest.fixture
def project_with_hardcoded_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a tmp project containing one hardcoded OpenAI URL, cd into it."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "client.py").write_text(
        'from openai import OpenAI\nclient = OpenAI(base_url="https://api.openai.com/v1")\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestScanCodeHappyFlow:
    def test_emits_finding_with_file_line_provider(self, project_with_hardcoded_url: Path) -> None:
        result = runner.invoke(app, ["scan", "--code", "--no-ai-prompt"])
        # Findings exit 0 — warn-only contract.
        assert result.exit_code == 0
        # Path appears in human output (stderr, like the .env scanner).
        assert "client.py" in result.stderr
        assert "https://api.openai.com/v1" in result.stderr
        assert "openai" in result.stderr.lower()
        assert "OPENAI_BASE_URL" in result.stderr
        # Source line snippet must be present (→ prefix from _format_code_findings_human).
        assert "→" in result.stderr

    def test_ai_prompt_block_emitted_by_default(self, project_with_hardcoded_url: Path) -> None:
        result = runner.invoke(app, ["scan", "--code"])
        assert result.exit_code == 0
        assert "COPY THIS TO YOUR AI AGENT" in result.stderr
        assert "OPENAI_BASE_URL" in result.stderr

    def test_ai_prompt_suppressed_with_flag(self, project_with_hardcoded_url: Path) -> None:
        result = runner.invoke(app, ["scan", "--code", "--no-ai-prompt"])
        assert result.exit_code == 0
        assert "COPY THIS TO YOUR AI AGENT" not in result.stderr

    def test_honesty_footer_present(self, project_with_hardcoded_url: Path) -> None:
        result = runner.invoke(app, ["scan", "--code", "--no-ai-prompt"])
        # Honest framing per feedback_honest_framing_audit.md — check specific bypass classes.
        assert "does NOT" in result.stderr
        assert "runtime-composed" in result.stderr
        assert "IP literals" in result.stderr

    def test_json_output_has_code_findings_key(self, project_with_hardcoded_url: Path) -> None:
        result = runner.invoke(app, ["scan", "--code", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "code_findings" in data
        assert len(data["code_findings"]) == 1
        f = data["code_findings"][0]
        assert f["provider_name"] == "openai"
        assert f["suggested_env_var"] == "OPENAI_BASE_URL"
        assert f["matched_url"] == "https://api.openai.com/v1"
        assert f["line"] == 2

    def test_json_output_excludes_ai_prompt(self, project_with_hardcoded_url: Path) -> None:
        result = runner.invoke(app, ["scan", "--code", "--json"])
        assert "COPY THIS TO YOUR AI AGENT" not in result.stdout


class TestScanCodeOptOut:
    def test_scan_without_code_flag_skips_code_scan(self, project_with_hardcoded_url: Path) -> None:
        # Plain ``worthless scan`` must NOT scan source — only .env.
        result = runner.invoke(app, ["scan"])
        # No .env present so exit 0; no code findings printed.
        assert "client.py" not in result.stderr
        assert "COPY THIS TO YOUR AI AGENT" not in result.stderr


class TestScanCodeBadFlow:
    def test_clean_project_zero_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "app.py").write_text(
            'import os\nurl = os.environ["OPENAI_BASE_URL"]\n', encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["scan", "--code", "--no-ai-prompt"])
        assert result.exit_code == 0
        assert "No hardcoded provider URLs" in result.stderr

    def test_nonexistent_path_errors_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["scan", "--code", "/does/not/exist"])
        assert result.exit_code != 0
        # No raw Python traceback leaking to the user.
        assert "Traceback" not in result.stderr


class TestScanCodeEdges:
    """Edges from the manual coverage audit — AI prompt suppression on
    zero-findings, and the documented SARIF-doesn't-include-code-findings
    contract."""

    def test_ai_prompt_block_not_emitted_when_zero_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Clean project + --code (with default --ai-prompt ON) should NOT
        # emit the COPY-THIS-TO-YOUR-AI-AGENT block. The block is for
        # actionable findings; on a clean run it would be noise.
        (tmp_path / "ok.py").write_text(
            'import os\nurl = os.environ.get("OPENAI_BASE_URL")\n', encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["scan", "--code"])
        assert result.exit_code == 0
        assert "COPY THIS TO YOUR AI AGENT" not in result.stderr

    def test_sarif_format_does_not_include_code_findings_by_design(
        self, project_with_hardcoded_url: Path
    ) -> None:
        # Documented limit: SARIF output is for .env key findings only.
        # Code findings live in the text + JSON envelopes. If a future
        # ticket wants code findings in SARIF, that's a deliberate
        # additive change, not a silent bug.
        import json

        result = runner.invoke(app, ["scan", "--code", "--format", "sarif"])
        assert result.exit_code == 0
        sarif = json.loads(result.stdout)
        # SARIF shape: {"runs": [{"results": [...]}]}; assert no
        # result is the new hardcoded-provider-url rule.
        all_rule_ids: set[str] = set()
        for run in sarif.get("runs", []):
            for r in run.get("results", []):
                if r.get("ruleId"):
                    all_rule_ids.add(r["ruleId"])
        assert "hardcoded-provider-url" not in all_rule_ids
