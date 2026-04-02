"""Tests for scanner — file scanning, entropy thresholding, decoy suppression."""

from __future__ import annotations

from pathlib import Path



class TestScanFinding:
    def test_dataclass_fields(self):
        from worthless.cli.scanner import ScanFinding

        f = ScanFinding(
            file="test.env", line=1, var_name="KEY", provider="openai",
            is_protected=False, value_preview="sk-****",
        )
        assert f.file == "test.env"
        assert f.line == 1
        assert f.provider == "openai"
        assert f.is_protected is False


class TestScanFiles:
    def test_detects_api_key_in_file(self, tmp_path: Path):
        from worthless.cli.scanner import scan_files

        f = tmp_path / ".env"
        f.write_text('OPENAI_API_KEY=sk-proj-a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2\n')
        findings = scan_files([f])
        assert len(findings) >= 1
        assert findings[0].provider == "openai"

    def test_skips_low_entropy(self, tmp_path: Path):
        from worthless.cli.scanner import scan_files

        f = tmp_path / ".env"
        f.write_text('OPENAI_API_KEY=sk-your-key-here\n')
        findings = scan_files([f])
        assert len(findings) == 0

    def test_decoy_low_entropy_skipped(self, tmp_path: Path):
        """Low-entropy decoys (WRTLS pattern) are filtered by entropy threshold."""
        from worthless.cli.scanner import scan_files

        f = tmp_path / ".env"
        # Decoy with low entropy — repeating WRTLS pattern after prefix
        decoy_value = "sk-proj-a1b2c3d4WRTLSWRTLSWRTLSWRTLSWRTLSWRTLSWRTLSWRTLS"
        f.write_text(f'OPENAI_API_KEY={decoy_value}\n')
        findings = scan_files([f])
        assert len(findings) == 0  # filtered by entropy

    def test_multiple_files(self, tmp_path: Path):
        from worthless.cli.scanner import scan_files

        f1 = tmp_path / "a.env"
        f2 = tmp_path / "b.env"
        f1.write_text('KEY1=sk-proj-a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8s9T0u1V2\n')
        f2.write_text('KEY2=sk-ant-api03-x9Y8w7V6u5T4s3R2q1P0o9N8m7L6k5J4i3H2g1F0e9\n')
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
                file="test.env", line=1, var_name="KEY", provider="openai",
                is_protected=False, value_preview="sk-****",
            ),
        ]
        sarif = format_sarif(findings, tool_version="0.1.0")
        assert sarif["$schema"] == "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"]) == 1
        assert len(sarif["runs"][0]["results"]) == 1


class TestEnrollmentDataRemoved:
    def test_load_enrollment_data_removed(self):
        """load_enrollment_data was dead code (shard_a is binary, not text) and has been removed."""
        import worthless.cli.scanner as scanner_mod

        assert not hasattr(scanner_mod, "load_enrollment_data")
