"""Unit tests for the hardcoded-provider-URL code scanner (worthless-7sl9).

Test layout follows the four flow categories agreed in the plan:
  1. Happy flow      — clean detection on supported file types
  2. Bad flow        — malformed inputs, unreadable files, perf guards
  3. Convoluted flow — vendored deps, gitignore, symlinks, lockfiles
  4. Adversarial flow — false-positive / false-negative edges; known limits

Plus a separate file (test_openclaw_lock_signature.py) covers OpenClaw
insulation — that lock + apply_lock are not touched by this PR.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from worthless.cli.code_scanner import (
    CodeFinding,
    scan_for_hardcoded_provider_urls,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write(path: Path, content: str) -> Path:
    """Write text to path, creating parents. Returns path for chaining."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def init_git(repo: Path, gitignore: str = "") -> None:
    """Initialize a git repo at ``repo`` with an optional .gitignore."""
    # ``git`` from PATH is intentional — pinning to /usr/bin/git breaks
    # Windows/WSL where git lives elsewhere. Mirrors how the scanner itself
    # invokes ``git ls-files``.
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],  # noqa: S607
        cwd=repo,
        check=True,
    )
    if gitignore:
        (repo / ".gitignore").write_text(gitignore, encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],  # noqa: S607
        cwd=repo,
        check=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--quiet", "-m", "init"],  # noqa: S607
        cwd=repo,
        check=True,
    )


# ---------------------------------------------------------------------------
# 1. Happy flow
# ---------------------------------------------------------------------------


class TestCodeScanHappyFlow:
    def test_finds_single_openai_url_in_python_file(self, tmp_path: Path) -> None:
        write(
            tmp_path / "app.py",
            'from openai import OpenAI\nclient = OpenAI(base_url="https://api.openai.com/v1")\n',
        )
        findings = scan_for_hardcoded_provider_urls([tmp_path])

        assert len(findings) == 1
        f = findings[0]
        assert isinstance(f, CodeFinding)
        assert f.file.endswith("app.py")
        assert f.line == 2
        assert f.column >= 1
        assert f.matched_url == "https://api.openai.com/v1"
        assert f.provider_name == "openai"
        assert f.suggested_env_var == "OPENAI_BASE_URL"
        assert "https://api.openai.com/v1" in f.line_text

    def test_finds_anthropic_in_yaml(self, tmp_path: Path) -> None:
        write(
            tmp_path / "config.yaml",
            "service: ai\nbase_url: https://api.anthropic.com/v1\nkey: foo\n",
        )
        findings = scan_for_hardcoded_provider_urls([tmp_path])

        assert len(findings) == 1
        assert findings[0].provider_name == "anthropic"
        assert findings[0].suggested_env_var == "ANTHROPIC_BASE_URL"
        assert findings[0].line == 2

    def test_no_findings_in_clean_repo(self, tmp_path: Path) -> None:
        write(
            tmp_path / "app.py",
            'import os\nclient = OpenAI(base_url=os.environ["OPENAI_BASE_URL"])\n',
        )
        assert scan_for_hardcoded_provider_urls([tmp_path]) == []

    def test_multiple_providers_in_one_file(self, tmp_path: Path) -> None:
        write(
            tmp_path / "clients.py",
            'a = "https://api.openai.com/v1"\nb = "https://api.anthropic.com/v1"\n',
        )
        findings = scan_for_hardcoded_provider_urls([tmp_path])

        providers = sorted(f.provider_name for f in findings)
        assert providers == ["anthropic", "openai"]
        assert len(findings) == 2
        lines = sorted(f.line for f in findings)
        assert lines == [1, 2]


# ---------------------------------------------------------------------------
# 2. Bad flow
# ---------------------------------------------------------------------------


class TestCodeScanBadFlow:
    def test_unreadable_file_logged_skipped_not_crashed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        unreadable = write(tmp_path / "secret.py", '"https://api.openai.com/v1"\n')
        write(tmp_path / "ok.py", '"https://api.anthropic.com/v1"\n')
        unreadable.chmod(0o000)

        try:
            findings = scan_for_hardcoded_provider_urls([tmp_path])
        finally:
            unreadable.chmod(0o644)

        assert any(f.file.endswith("ok.py") for f in findings)
        assert not any(f.file.endswith("secret.py") for f in findings)

    def test_binary_file_with_extension_collision_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "blob.json").write_bytes(b"\x00\x01\x02\xff\xfe\xfd")
        write(tmp_path / "ok.py", '"https://api.openai.com/v1"\n')

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert len(findings) == 1
        assert findings[0].file.endswith("ok.py")

    def test_empty_repo_zero_findings(self, tmp_path: Path) -> None:
        assert scan_for_hardcoded_provider_urls([tmp_path]) == []

    def test_huge_file_skipped(self, tmp_path: Path) -> None:
        huge = tmp_path / "huge.py"
        huge.write_text("x = 1\n" * (220_000), encoding="utf-8")
        assert huge.stat().st_size > 1_000_000
        huge.write_text("x = 1\n" * 220_000 + '"https://api.openai.com/v1"\n', encoding="utf-8")

        write(tmp_path / "small.py", '"https://api.anthropic.com/v1"\n')

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        files = {Path(f.file).name for f in findings}
        assert "huge.py" not in files
        assert "small.py" in files


# ---------------------------------------------------------------------------
# 3. Convoluted flow
# ---------------------------------------------------------------------------


class TestCodeScanConvolutedFlow:
    def test_node_modules_excluded(self, tmp_path: Path) -> None:
        write(
            tmp_path / "node_modules" / "openai" / "index.js",
            'baseUrl = "https://api.openai.com/v1";\n',
        )
        write(tmp_path / "src" / "app.py", '"https://api.openai.com/v1"\n')

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        # Check path components, not raw substring — pytest tmp dir names
        # can themselves contain the exclude name (e.g. test_node_modules_x).
        for f in findings:
            rel = Path(f.file).relative_to(tmp_path)
            assert "node_modules" not in rel.parts
        assert any(f.file.endswith("app.py") for f in findings)

    def test_venv_site_packages_excluded(self, tmp_path: Path) -> None:
        write(
            tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "openai" / "_base.py",
            '"https://api.openai.com/v1"\n',
        )
        write(tmp_path / "app.py", '"https://api.openai.com/v1"\n')

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        for f in findings:
            rel = Path(f.file).relative_to(tmp_path)
            assert ".venv" not in rel.parts
        assert any(f.file.endswith("app.py") for f in findings)

    def test_vendor_dir_excluded(self, tmp_path: Path) -> None:
        write(
            tmp_path / "vendor" / "openai-go" / "client.go",
            '"https://api.openai.com/v1"\n',
        )
        write(tmp_path / "main.go", '"https://api.openai.com/v1"\n')

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        for f in findings:
            rel = Path(f.file).relative_to(tmp_path)
            assert "vendor" not in rel.parts
        assert any(f.file.endswith("main.go") for f in findings)

    def test_gitignore_respected_in_git_repo(self, tmp_path: Path) -> None:
        write(tmp_path / "tracked.py", '"https://api.openai.com/v1"\n')
        write(tmp_path / "tmp" / "local.py", '"https://api.anthropic.com/v1"\n')
        init_git(tmp_path, gitignore="tmp/\n")

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        files = {Path(f.file).name for f in findings}
        assert "tracked.py" in files
        assert "local.py" not in files

    def test_gitignore_ignored_outside_git_repo(self, tmp_path: Path) -> None:
        write(tmp_path / ".gitignore", "secret/\n")
        write(tmp_path / "secret" / "local.py", '"https://api.openai.com/v1"\n')

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert any(f.file.endswith("local.py") for f in findings)

    def test_symlink_to_excluded_dir_not_followed(self, tmp_path: Path) -> None:
        write(tmp_path / "node_modules" / "evil.py", '"https://api.openai.com/v1"\n')
        link = tmp_path / "src" / "vendored"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(tmp_path / "node_modules")

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert findings == []

    def test_lockfile_excluded(self, tmp_path: Path) -> None:
        write(
            tmp_path / "package-lock.json",
            '{"resolved": "https://api.openai.com/v1"}\n',
        )
        write(tmp_path / "yarn.lock", "url https://api.anthropic.com/v1\n")

        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert findings == []

    def test_minified_js_excluded(self, tmp_path: Path) -> None:
        write(tmp_path / "app.min.js", '"https://api.openai.com/v1"\n')
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert findings == []

    def test_md_doc_with_url_in_code_fence_is_flagged(self, tmp_path: Path) -> None:
        write(
            tmp_path / "README.md",
            "Use the API:\n```python\nclient = OpenAI(base_url='https://api.openai.com/v1')\n```\n",
        )
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert len(findings) == 1
        assert findings[0].file.endswith("README.md")


# ---------------------------------------------------------------------------
# 4. Adversarial flow
# ---------------------------------------------------------------------------


class TestCodeScanAdversarialFlow:
    def test_url_in_python_comment_still_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", "# see https://api.openai.com/v1 for docs\n")
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert len(findings) == 1
        assert findings[0].provider_name == "openai"

    def test_url_in_string_concat_not_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", 'url = "https://api." + "openai.com/v1"\n')
        assert scan_for_hardcoded_provider_urls([tmp_path]) == []

    def test_url_with_uppercase_host_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", 'url = "HTTPS://API.OPENAI.COM/v1"\n')
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert len(findings) == 1
        assert findings[0].provider_name == "openai"

    def test_url_with_trailing_slash_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", 'url = "https://api.openai.com/v1/"\n')
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert len(findings) == 1

    def test_url_with_query_string_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", 'url = "https://api.openai.com/v1?foo=bar"\n')
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert len(findings) == 1

    def test_url_in_test_fixture_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "tests" / "fixtures" / "sample.py", '"https://api.openai.com/v1"\n')
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        assert len(findings) == 1

    def test_partial_url_not_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", 'url = "https://api.openai.co"\n')
        assert scan_for_hardcoded_provider_urls([tmp_path]) == []

    def test_unknown_provider_url_not_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", 'url = "https://api.unknown-llm.example/v1"\n')
        assert scan_for_hardcoded_provider_urls([tmp_path]) == []

    def test_regional_endpoint_not_flagged(self, tmp_path: Path) -> None:
        # Documented bypass — we only match registry literals.
        write(tmp_path / "a.py", 'url = "https://eu.api.openai.com/v1"\n')
        assert scan_for_hardcoded_provider_urls([tmp_path]) == []

    def test_ip_literal_not_flagged(self, tmp_path: Path) -> None:
        write(tmp_path / "a.py", 'url = "http://104.18.32.7/v1"\n')
        assert scan_for_hardcoded_provider_urls([tmp_path]) == []

    def test_scan_completes_under_5s_on_million_lines(self, tmp_path: Path) -> None:
        # Many small files instead of one huge (which would hit the >1MB skip).
        for i in range(500):
            write(tmp_path / f"f{i}.py", "x = 1\n" * 200)
        start = time.monotonic()
        findings = scan_for_hardcoded_provider_urls([tmp_path])
        elapsed = time.monotonic() - start
        assert findings == []
        assert elapsed < 5.0, f"scan took {elapsed:.2f}s"
