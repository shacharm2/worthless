"""Tests for scanner — file scanning, entropy thresholding, decoy suppression."""

from __future__ import annotations

from pathlib import Path


class TestScanFinding:
    def test_dataclass_fields(self):
        from worthless.cli.scanner import ScanFinding

        f = ScanFinding(
            file="test.env",
            line=1,
            var_name="KEY",
            provider="openai",
            is_protected=False,
            value_preview="sk-****",
        )
        assert f.file == "test.env"
        assert f.line == 1
        assert f.provider == "openai"
        assert f.is_protected is False


class TestScanFiles:
    def test_detects_api_key_in_file(self, tmp_path: Path):
        from worthless.cli.scanner import scan_files

        f = tmp_path / ".env"
        f.write_text("OPENAI_API_KEY=sk-proj-a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2\n")
        findings = scan_files([f])
        assert len(findings) >= 1
        assert findings[0].provider == "openai"

    def test_skips_low_entropy(self, tmp_path: Path):
        from worthless.cli.scanner import scan_files

        f = tmp_path / ".env"
        f.write_text("OPENAI_API_KEY=sk-your-key-here\n")
        findings = scan_files([f])
        assert len(findings) == 0

    def test_decoy_low_entropy_skipped(self, tmp_path: Path):
        """Low-entropy decoys (WRTLS pattern) are filtered by entropy threshold."""
        from worthless.cli.scanner import scan_files

        f = tmp_path / ".env"
        # Decoy with low entropy — repeating WRTLS pattern after prefix
        decoy_value = "sk-proj-a1b2c3d4WRTLSWRTLSWRTLSWRTLSWRTLSWRTLSWRTLSWRTLS"
        f.write_text(f"OPENAI_API_KEY={decoy_value}\n")
        findings = scan_files([f])
        assert len(findings) == 0  # filtered by entropy

    def test_multiple_files(self, tmp_path: Path):
        from worthless.cli.scanner import scan_files

        f1 = tmp_path / "a.env"
        f2 = tmp_path / "b.env"
        f1.write_text("KEY1=sk-proj-a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2\n")
        f2.write_text("KEY2=sk-ant-api03-x9Y8w7V6u5T4s3R2q1P0o9N8m7L6k5J4i3H2g1F0e9\n")
        findings = scan_files([f1, f2])
        assert len(findings) == 2

    def test_non_env_file(self, tmp_path: Path):
        from worthless.cli.scanner import scan_files

        f = tmp_path / "config.py"
        f.write_text('API_KEY = "sk-proj-a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2"\n')
        findings = scan_files([f])
        assert len(findings) >= 1


class TestSarifOutput:
    def test_sarif_format(self, tmp_path: Path):
        from worthless.cli.scanner import ScanFinding, format_sarif

        findings = [
            ScanFinding(
                file="test.env",
                line=1,
                var_name="KEY",
                provider="openai",
                is_protected=False,
                value_preview="sk-****",
            ),
        ]
        sarif = format_sarif(findings, tool_version="0.1.0")
        assert (
            sarif["$schema"]
            == "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
        )
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"]) == 1
        assert len(sarif["runs"][0]["results"]) == 1


class TestEnrollmentDataRemoved:
    def test_load_enrollment_data_removed(self):
        """load_enrollment_data was dead code (shard_a is binary, not text) and has been removed."""
        import worthless.cli.scanner as scanner_mod

        assert not hasattr(scanner_mod, "load_enrollment_data")


class TestScanGuards:
    """Hang-guards for scan_files (worthless-c5kc).

    A pre-commit hook calling ``worthless scan`` must NOT silently freeze on
    a huge / slow / unreadable file. The contract these tests pin:
      * oversize file → its prefix is still scanned + flagged ``truncated``
        (so a key padded past the cap is still caught);
      * past-deadline → scanning stops, findings so far are returned, and a
        ``timeout`` skip is recorded (caller's job to fail-closed on it);
      * unreadable file → recorded as ``unreadable`` instead of being silently
        ``except OSError: continue``-d;
      * normal small tree → no skips, behaviour unchanged.
    """

    # A high-entropy key that KEY_PATTERN + entropy threshold both accept.
    REAL_KEY = "sk-proj-a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2"

    def test_oversize_file_prefix_still_scanned_and_flagged_truncated(self, tmp_path: Path):
        from worthless.cli.scanner import SkippedFile, scan_files

        # Put the key in the FIRST 1 KB, then pad past a tiny cap. The prefix
        # read must still yield a finding AND record ``truncated``.
        f = tmp_path / "huge.env"
        f.write_bytes(f"OPENAI_API_KEY={self.REAL_KEY}\n".encode() + b"x" * (8 * 1024))

        skipped: list[SkippedFile] = []
        findings = scan_files([f], max_file_bytes=2048, skipped=skipped)

        assert any(x.provider == "openai" for x in findings), (
            "prefix-scan must still catch a key in the first ``max_file_bytes`` bytes"
        )
        assert [(s.file, s.reason) for s in skipped] == [(str(f), "truncated")]

    def test_past_deadline_returns_partial_and_records_timeout(self, tmp_path: Path):
        import time

        from worthless.cli.scanner import SkippedFile, scan_files

        a = tmp_path / "a.env"
        b = tmp_path / "b.env"
        a.write_text(f"OPENAI_API_KEY={self.REAL_KEY}\n")
        b.write_text(f"ANTHROPIC_API_KEY={self.REAL_KEY}\n")

        # Deadline already in the past — first iteration must short-circuit
        # before reading a.env. ``findings`` is empty, ``skipped`` carries
        # exactly ONE timeout entry (the file that would have been scanned next).
        past = time.monotonic() - 1.0
        skipped: list[SkippedFile] = []
        findings = scan_files([a, b], deadline=past, skipped=skipped)

        assert findings == []
        assert len(skipped) == 1
        assert skipped[0].reason == "timeout"

    def test_partial_findings_when_deadline_hits_mid_loop(self, tmp_path: Path, monkeypatch):
        """First file scanned, deadline trips before second → 1 finding + 1 timeout skip."""
        import worthless.cli.scanner as scanner_mod
        from worthless.cli.scanner import SkippedFile, scan_files

        a = tmp_path / "a.env"
        b = tmp_path / "b.env"
        a.write_text(f"OPENAI_API_KEY={self.REAL_KEY}\n")
        b.write_text(f"ANTHROPIC_API_KEY={self.REAL_KEY}\n")

        # Fake clock: first call (a's deadline check) returns 0.0, every later
        # call returns 100.0 — so b's deadline check trips no matter how many
        # ``time.monotonic()`` calls scanner.py adds in the future (logging,
        # telemetry, etc.). Robust to refactors.
        calls = {"n": 0}

        def fake_monotonic() -> float:
            calls["n"] += 1
            return 0.0 if calls["n"] == 1 else 100.0

        monkeypatch.setattr(scanner_mod.time, "monotonic", fake_monotonic)

        skipped: list[SkippedFile] = []
        findings = scan_files([a, b], deadline=1.0, skipped=skipped)

        assert len(findings) == 1, "first file must have been scanned before deadline trip"
        assert findings[0].file == str(a)
        assert [s.reason for s in skipped] == ["timeout"]
        assert skipped[0].file == str(b)

    def test_nonexistent_file_silently_skipped(self, tmp_path: Path):
        """A path that doesn't exist (typo or git-rm'd in pre-commit) is NOT a
        hang risk — it's just absent. Stays silent so the pre-commit flow over
        deleted files is unchanged."""
        from worthless.cli.scanner import SkippedFile, scan_files

        missing = tmp_path / "does-not-exist.env"
        skipped: list[SkippedFile] = []
        findings = scan_files([missing], skipped=skipped)

        assert findings == []
        assert skipped == []

    def test_unreadable_existing_path_recorded(self, tmp_path: Path):
        """A path that EXISTS but can't be read (here: a directory) IS a fail-
        closed concern — we don't know what we missed. Recorded as ``unreadable``."""
        from worthless.cli.scanner import SkippedFile, scan_files

        # Passing a directory triggers IsADirectoryError, a subclass of OSError
        # but NOT FileNotFoundError. Cross-platform; no chmod-tricks needed.
        a_dir = tmp_path / "subdir"
        a_dir.mkdir()

        skipped: list[SkippedFile] = []
        findings = scan_files([a_dir], skipped=skipped)

        assert findings == []
        assert [(s.file, s.reason) for s in skipped] == [(str(a_dir), "unreadable")]

    def test_normal_small_tree_no_skips(self, tmp_path: Path):
        from worthless.cli.scanner import SkippedFile, scan_files

        f = tmp_path / ".env"
        f.write_text(f"OPENAI_API_KEY={self.REAL_KEY}\n")

        skipped: list[SkippedFile] = []
        findings = scan_files([f], skipped=skipped)

        assert len(findings) == 1
        assert skipped == [], "a normal small file must not produce any skip entries"
