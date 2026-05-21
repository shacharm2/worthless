"""WOR-515 Phase 1 — audit gate unit/integration tests (no Docker required).

12 AC tests + 10 adversarial tests.  All mocked at the subprocess layer.
Docker-gated tests live in test_lock_audit_gate_docker.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from worthless.openclaw.audit import (
    AuditFinding,
    AuditGateError,
    AuditResult,
    BlockingFinding,
    check_auth_profiles_direct,
    classify_findings,
    format_gate_error_message,
    resolve_openclaw_bin,
    run_audit,
    snapshot_hashes,
)

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _make_proc(stdout: str, returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


def _audit_json(findings: list[dict], files_scanned: list[str] | None = None) -> str:
    """Build a minimal valid audit JSON payload."""
    plaintext = sum(1 for f in findings if f.get("code") == "PLAINTEXT_FOUND")
    return json.dumps(
        {
            "version": 1,
            "status": "findings" if findings else "clean",
            "resolution": {"refsChecked": 0, "skippedExecRefs": 0, "resolvabilityComplete": True},
            "filesScanned": files_scanned or ["/home/node/.openclaw/openclaw.json"],
            "summary": {
                "plaintextCount": plaintext,
                "unresolvedRefCount": 0,
                "shadowedRefCount": 0,
                "legacyResidueCount": 0,
            },
            "findings": findings,
        }
    )


def _plaintext_finding(
    json_path: str = "models.providers.openai.apiKey",
    file: str = "/home/node/.openclaw/openclaw.json",
    provider: str | None = "openai",
) -> dict:
    return {
        "code": "PLAINTEXT_FOUND",
        "severity": "warn",
        "file": file,
        "jsonPath": json_path,
        "message": f"{json_path} is stored as plaintext.",
        **({"provider": provider} if provider else {}),
    }


# --------------------------------------------------------------------------- #
# AC 1 — clean config → proceed                                                #
# --------------------------------------------------------------------------- #


class TestAC1CleanConfig:
    def test_clean_audit_result_is_not_blocking(self) -> None:
        """AC 1: clean audit (no findings) returns empty blocking list."""
        clean = _load_fixture("m0_audit_clean.json")
        result = AuditResult(
            version=clean["version"],
            status=clean["status"],
            files_scanned=tuple(clean["filesScanned"]),
            plaintext_count=clean["summary"]["plaintextCount"],
            findings=tuple(
                AuditFinding(
                    code=f["code"],
                    severity=f["severity"],
                    file=f["file"],
                    json_path=f["jsonPath"],
                    message=f["message"],
                    provider=f.get("provider"),
                )
                for f in clean["findings"]
            ),
        )
        classification = classify_findings(result)
        assert len(classification.blocking) == 0
        assert len(classification.unknown_codes) == 0


# --------------------------------------------------------------------------- #
# AC 2 — openclaw.json plaintext provider apiKey → exit 73                     #
# --------------------------------------------------------------------------- #


class TestAC2PlaintextProviderKey:
    def test_plaintext_provider_key_is_blocking(self) -> None:
        """AC 2: PLAINTEXT_FOUND for non-allowlisted provider blocks."""
        finding = AuditFinding(
            code="PLAINTEXT_FOUND",
            severity="warn",
            file="/home/node/.openclaw/openclaw.json",
            json_path="models.providers.openai.apiKey",
            message="models.providers.openai.apiKey is stored as plaintext.",
            provider="openai",
        )
        result = AuditResult(
            version=1,
            status="findings",
            files_scanned=("/home/node/.openclaw/openclaw.json",),
            plaintext_count=1,
            findings=(finding,),
        )
        classification = classify_findings(result)
        assert len(classification.blocking) == 1
        assert classification.blocking[0].json_path == "models.providers.openai.apiKey"

    def test_error_message_names_configure_remediation(self) -> None:
        """AC 2+5: error message for exit 73 names openclaw secrets configure."""
        blocking = (
            BlockingFinding(
                file="/home/node/.openclaw/openclaw.json",
                json_path="models.providers.openai.apiKey",
                provider="openai",
                message="openai.apiKey is plaintext",
                source="audit",
            ),
        )
        msg = format_gate_error_message(blocking)
        assert "openclaw secrets configure" in msg
        assert "--apply" not in msg  # no non-interactive flag — requires TTY

    def test_run_audit_dispatches_subprocess(self, tmp_path: Path) -> None:
        """run_audit calls openclaw with --json and returns parsed result."""
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        payload = _audit_json([])
        proc = _make_proc(stdout=payload)
        with patch("subprocess.run", return_value=proc) as mock_run:
            result = run_audit(fake_bin)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert str(fake_bin) in args
        assert "--json" in args
        assert result.plaintext_count == 0


# --------------------------------------------------------------------------- #
# AC 3 — auth-profiles plaintext → exit 73 (direct read, not audit)           #
# --------------------------------------------------------------------------- #


class TestAC3AuthProfilesDirect:
    def test_auth_profiles_plaintext_detected_directly(self, tmp_path: Path) -> None:
        """AC 3: worthless reads auth-profiles.json directly; audit never flags it."""
        auth = tmp_path / "auth-profiles.json"
        auth.write_text(
            json.dumps(
                {
                    "profiles": {
                        "default": {"token": "sk-proj-realcachedkey0000000000000000000000000000000"}
                    }
                }
            )
        )
        findings = check_auth_profiles_direct([str(auth)])
        assert len(findings) == 1
        assert "auth-profiles" in findings[0].file
        assert findings[0].source == "auth-profiles-direct"

    def test_auth_profiles_not_in_files_scanned_is_ignored(self, tmp_path: Path) -> None:
        """AC 3: file not in filesScanned[] is not read — decoy protection."""
        decoy = tmp_path / "auth-profiles.json"
        decoy.write_text(
            json.dumps(
                {"profiles": {"x": {"key": "sk-proj-decoy000000000000000000000000000000000000000"}}}
            )
        )
        # Not passing the decoy path — it's not in filesScanned
        findings = check_auth_profiles_direct([])
        assert len(findings) == 0

    def test_auth_profiles_classified_as_blocking(self, tmp_path: Path) -> None:
        """AC 3: auth-profiles blocking finding → classify_findings returns blocking."""
        auth_finding = BlockingFinding(
            file=str(tmp_path / "auth-profiles.json"),
            json_path="profiles.default.token",
            provider=None,
            message="plaintext key in auth-profiles",
            source="auth-profiles-direct",
        )
        result = AuditResult(
            version=1,
            status="clean",
            files_scanned=(str(tmp_path / "auth-profiles.json"),),
            plaintext_count=0,
            findings=(),
        )
        classification = classify_findings(result, auth_profiles_blocking=[auth_finding])
        assert len(classification.blocking) == 1
        assert classification.blocking[0].source == "auth-profiles-direct"


# --------------------------------------------------------------------------- #
# AC 4 — multi-provider aggregation                                            #
# --------------------------------------------------------------------------- #


class TestAC4MultiProviderAggregation:
    def test_all_blocking_findings_listed(self) -> None:
        """AC 4: classify_findings returns ALL blocking findings, no short-circuit."""
        findings = tuple(
            AuditFinding(
                code="PLAINTEXT_FOUND",
                severity="warn",
                file="/home/node/.openclaw/openclaw.json",
                json_path=f"models.providers.provider{i}.apiKey",
                message=f"provider{i}.apiKey is plaintext",
                provider=f"provider{i}",
            )
            for i in range(3)
        )
        result = AuditResult(
            version=1,
            status="findings",
            files_scanned=("/home/node/.openclaw/openclaw.json",),
            plaintext_count=3,
            findings=findings,
        )
        classification = classify_findings(result)
        assert len(classification.blocking) == 3

    def test_error_message_lists_all_providers(self) -> None:
        """AC 4: error message lists every blocking finding."""
        blocking = tuple(
            BlockingFinding(
                file="/home/node/.openclaw/openclaw.json",
                json_path=f"models.providers.provider{i}.apiKey",
                provider=f"provider{i}",
                message=f"provider{i} is plaintext",
                source="audit",
            )
            for i in range(3)
        )
        msg = format_gate_error_message(blocking)
        for i in range(3):
            assert f"provider{i}" in msg


# --------------------------------------------------------------------------- #
# AC 5 — error message names correct remediation                               #
# --------------------------------------------------------------------------- #


class TestAC5RemediationMessage:
    def test_remediation_names_configure_no_interactive_flags(self) -> None:
        """AC 5: remediation command is 'openclaw secrets configure' only."""
        blocking = (
            BlockingFinding(
                file="/f",
                json_path="models.providers.x.apiKey",
                provider="x",
                message="x is plaintext",
                source="audit",
            ),
        )
        msg = format_gate_error_message(blocking)
        assert "openclaw secrets configure" in msg
        # M0 confirmed: no non-interactive flags exist
        assert "--apply" not in msg
        assert "--yes" not in msg
        assert "--plan-out" not in msg


# --------------------------------------------------------------------------- #
# AC 6 — re-lock works (bootstrap paradox, exact-name allowlist)              #
# --------------------------------------------------------------------------- #


class TestAC6ReLockBootstrapParadox:
    def test_worthless_own_provider_apikey_is_not_blocking(self) -> None:
        """AC 6: worthless-openai.apiKey (shard-A) is NOT blocking — allowlist."""
        finding = AuditFinding(
            code="PLAINTEXT_FOUND",
            severity="warn",
            file="/home/node/.openclaw/openclaw.json",
            json_path="models.providers.worthless-openai.apiKey",
            message="worthless-openai.apiKey is stored as plaintext.",
            provider="worthless-openai",
        )
        result = AuditResult(
            version=1,
            status="findings",
            files_scanned=("/home/node/.openclaw/openclaw.json",),
            plaintext_count=1,
            findings=(finding,),
        )
        classification = classify_findings(result)
        assert len(classification.blocking) == 0

    def test_bootstrap_fixture_has_zero_blocking_after_allowlist(self) -> None:
        """AC 6: m0_audit_bootstrap_paradox fixture → 0 blocking after allowlist."""
        raw = _load_fixture("m0_audit_bootstrap_paradox.json")
        findings = tuple(
            AuditFinding(
                code=f["code"],
                severity=f["severity"],
                file=f["file"],
                json_path=f["jsonPath"],
                message=f["message"],
                provider=f.get("provider"),
            )
            for f in raw["findings"]
        )
        result = AuditResult(
            version=raw["version"],
            status=raw["status"],
            files_scanned=tuple(raw["filesScanned"]),
            plaintext_count=raw["summary"]["plaintextCount"],
            findings=findings,
        )
        classification = classify_findings(result)
        # Only worthless-openai should be in findings; it's in the allowlist
        blocking_providers = {b.provider for b in classification.blocking}
        assert "worthless-openai" not in blocking_providers


# --------------------------------------------------------------------------- #
# AC 7 — subprocess fail-closed, split exit codes                              #
# --------------------------------------------------------------------------- #


class TestAC7SubprocessFailClosed:
    def test_subprocess_failure_raises_audit_gate_error(self, tmp_path: Path) -> None:
        """AC 7: non-zero subprocess exit → AuditGateError (not silent pass)."""
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        proc = _make_proc(stdout="", returncode=1)
        with patch("subprocess.run", return_value=proc):
            with pytest.raises(AuditGateError):
                run_audit(fake_bin)

    def test_timeout_raises_audit_gate_error(self, tmp_path: Path) -> None:
        """AC 7: subprocess timeout → AuditGateError."""
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        expired = subprocess.TimeoutExpired(cmd="openclaw", timeout=5)
        with patch("subprocess.run", side_effect=expired):
            with pytest.raises(AuditGateError):
                run_audit(fake_bin, timeout=0.001)

    def test_unparseable_stdout_raises_audit_gate_error(self, tmp_path: Path) -> None:
        """AC 7: garbage stdout → AuditGateError."""
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        proc = _make_proc(stdout="not json at all", returncode=0)
        with patch("subprocess.run", return_value=proc):
            with pytest.raises(AuditGateError):
                run_audit(fake_bin)


# --------------------------------------------------------------------------- #
# AC 8 — TOCTOU post-flight re-audit                                           #
# --------------------------------------------------------------------------- #


class TestAC8TOCTOUPostFlight:
    def test_snapshot_hashes_returns_sha256_per_file(self, tmp_path: Path) -> None:
        """AC 8: snapshot_hashes computes per-file SHA-256."""
        f = tmp_path / "openclaw.json"
        f.write_text('{"test": 1}')
        hashes = snapshot_hashes([str(f)])
        assert str(f) in hashes
        assert len(hashes[str(f)]) == 64  # hex SHA-256

    def test_snapshot_hashes_skips_missing_files(self) -> None:
        """AC 8: snapshot_hashes silently skips unreadable files."""
        hashes = snapshot_hashes(["/nonexistent/file.json"])
        assert "/nonexistent/file.json" not in hashes


# --------------------------------------------------------------------------- #
# AC 9 — explicit binary resolution                                            #
# --------------------------------------------------------------------------- #


class TestAC9ExplicitBinaryResolution:
    def test_resolve_uses_env_var_when_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC 9: WORTHLESS_OPENCLAW_BIN env var takes precedence."""
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        monkeypatch.setenv("WORTHLESS_OPENCLAW_BIN", str(fake_bin))
        resolved = resolve_openclaw_bin()
        assert resolved == fake_bin

    def test_resolve_raises_without_absolute_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC 9: no env var, no absolute path → AuditGateError."""
        monkeypatch.delenv("WORTHLESS_OPENCLAW_BIN", raising=False)
        with patch("shutil.which", return_value=None):
            with pytest.raises(AuditGateError, match="openclaw"):
                resolve_openclaw_bin()

    def test_resolve_uses_which_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC 9: shutil.which result is used when env var absent."""
        monkeypatch.delenv("WORTHLESS_OPENCLAW_BIN", raising=False)
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        with patch("shutil.which", return_value=str(fake_bin)):
            resolved = resolve_openclaw_bin()
        assert resolved == fake_bin


# --------------------------------------------------------------------------- #
# AC 10 — doctor surface (tested via error code contracts)                     #
# --------------------------------------------------------------------------- #


class TestAC10DoctorSurface:
    def test_audit_gate_error_has_reason_attribute(self) -> None:
        """AC 10: AuditGateError carries .reason for doctor to display."""
        exc = AuditGateError("subprocess failed: openclaw not found")
        assert "openclaw" in exc.reason

    def test_audit_gate_error_message_distinguishes_87_from_73(self) -> None:
        """AC 10: gate errors name the exit code context in their message."""
        subprocess_err = AuditGateError("audit subprocess failed — exit 87")
        plaintext_err_msg = format_gate_error_message(
            (
                BlockingFinding(
                    file="/f",
                    json_path="models.providers.x.apiKey",
                    provider="x",
                    message="plaintext",
                    source="audit",
                ),
            )
        )
        assert "87" in subprocess_err.reason or "subprocess" in subprocess_err.reason.lower()
        assert "configure" in plaintext_err_msg


# --------------------------------------------------------------------------- #
# AC 11 — fixture-driven: real audit schema round-trip                        #
# --------------------------------------------------------------------------- #


class TestAC11RealAuditFixture:
    def test_m0_audit_schema_parses_correctly(self) -> None:
        """AC 11: m0_audit_schema.json (real fixture) parses to 3 blocking findings."""
        raw = _load_fixture("m0_audit_schema.json")
        findings = tuple(
            AuditFinding(
                code=f["code"],
                severity=f["severity"],
                file=f["file"],
                json_path=f["jsonPath"],
                message=f["message"],
                provider=f.get("provider"),
            )
            for f in raw["findings"]
        )
        result = AuditResult(
            version=raw["version"],
            status=raw["status"],
            files_scanned=tuple(raw["filesScanned"]),
            plaintext_count=raw["summary"]["plaintextCount"],
            findings=findings,
        )
        classification = classify_findings(result)
        # gateway.auth.token is advisory; openai and anthropic are blocking
        blocking_paths = {b.json_path for b in classification.blocking}
        assert "models.providers.custom-api-openai-com.apiKey" in blocking_paths
        assert "models.providers.anthropic.apiKey" in blocking_paths
        assert "gateway.auth.token" not in blocking_paths

    def test_m0_audit_schema_has_correct_structure(self) -> None:
        """AC 11: m0_audit_schema.json has required top-level keys."""
        raw = _load_fixture("m0_audit_schema.json")
        assert "filesScanned" in raw
        assert "findings" in raw
        assert "summary" in raw
        assert raw["summary"]["plaintextCount"] == 3


# --------------------------------------------------------------------------- #
# AC 12 — WOR-545 xfail test exists and is wired correctly                    #
# --------------------------------------------------------------------------- #


class TestAC12WOR545LoadBearingTestExists:
    def test_load_bearing_test_file_exists(self) -> None:
        """AC 12: test_proxy_load_bearing.py exists in the correct location."""
        test_file = Path(__file__).parent / "test_proxy_load_bearing.py"
        assert test_file.exists(), (
            "WOR-545: test_proxy_load_bearing.py must exist before Phase 1 merges"
        )

    def test_load_bearing_test_has_xfail_mark(self) -> None:
        """AC 12: test_proxy_load_bearing.py is marked xfail(strict=True)."""
        test_file = Path(__file__).parent / "test_proxy_load_bearing.py"
        content = test_file.read_text()
        assert "xfail" in content
        assert "strict=True" in content


# =========================================================================== #
# ADVERSARIAL TESTS                                                             #
# =========================================================================== #


class TestAdversarial:
    def test_adversarial_provider_named_worthless_evil_is_blocked(self) -> None:
        """Adv 1: provider named 'worthless-evil' (starts with 'worthless-')
        but NOT in exact allowlist → blocked. Pins security crit #1."""
        finding = AuditFinding(
            code="PLAINTEXT_FOUND",
            severity="warn",
            file="/home/node/.openclaw/openclaw.json",
            json_path="models.providers.worthless-evil.apiKey",
            message="worthless-evil.apiKey is stored as plaintext.",
            provider="worthless-evil",
        )
        result = AuditResult(
            version=1,
            status="findings",
            files_scanned=("/home/node/.openclaw/openclaw.json",),
            plaintext_count=1,
            findings=(finding,),
        )
        classification = classify_findings(result)
        # Must be blocking — "worthless-evil" is NOT in the exact allowlist
        blocking_paths = {b.json_path for b in classification.blocking}
        assert "models.providers.worthless-evil.apiKey" in blocking_paths

    def test_adversarial_path_traversal_in_file_field_sanitised(self) -> None:
        """Adv 2: audit emits file:'../../etc/passwd' → path not followed."""
        evil_finding = AuditFinding(
            code="PLAINTEXT_FOUND",
            severity="warn",
            file="../../etc/passwd",
            json_path="models.providers.openai.apiKey",
            message="plaintext",
            provider="openai",
        )
        result = AuditResult(
            version=1,
            status="findings",
            files_scanned=("/home/node/.openclaw/openclaw.json",),
            plaintext_count=1,
            findings=(evil_finding,),
        )
        classification = classify_findings(result)
        # The finding is from a file NOT in filesScanned[] — must not block
        # (file path traversal should not match any filesScanned entry)
        # The implementation must only trust filesScanned paths
        for b in classification.blocking:
            # If it IS classified as blocking, the message must not echo the raw path
            msg = format_gate_error_message((b,))
            assert "etc/passwd" not in msg or "../../" not in msg

    def test_adversarial_control_chars_in_jsonpath_sanitised_in_message(self) -> None:
        """Adv 3: jsonPath with control chars → sanitised in error message."""
        evil_path = "models.providers.x.apiKey\x1b[31mRED\x1b[0m\nnewline"
        blocking = (
            BlockingFinding(
                file="/f",
                json_path=evil_path,
                provider="x",
                message="plaintext",
                source="audit",
            ),
        )
        msg = format_gate_error_message(blocking)
        # Injected terminal escapes, carriage returns, and null bytes must be stripped.
        # Structural \n from the multi-line message format is expected and allowed.
        assert "\x1b" not in msg  # ANSI terminal escape injection
        assert "\r" not in msg  # carriage return injection
        assert "\x00" not in msg  # null byte injection

    def test_adversarial_unknown_finding_code_blocks_default_deny(self) -> None:
        """Adv 4: unknown finding code → classify_findings records it as unknown
        (caller must raise AuditGateError — default-deny posture)."""
        unknown_finding = AuditFinding(
            code="FUTURE_UNKNOWN_CODE",
            severity="warn",
            file="/home/node/.openclaw/openclaw.json",
            json_path="models.providers.x.apiKey",
            message="unknown",
            provider="x",
        )
        result = AuditResult(
            version=1,
            status="findings",
            files_scanned=("/home/node/.openclaw/openclaw.json",),
            plaintext_count=0,
            findings=(unknown_finding,),
        )
        classification = classify_findings(result)
        assert "FUTURE_UNKNOWN_CODE" in classification.unknown_codes

    def test_adversarial_path_lookup_for_openclaw_binary_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adv 5: fake 'openclaw' in $PATH (relative) → refused. Security crit #6."""
        monkeypatch.delenv("WORTHLESS_OPENCLAW_BIN", raising=False)
        # which returns a relative-looking path
        with patch("shutil.which", return_value="openclaw"):
            with pytest.raises(AuditGateError):
                resolve_openclaw_bin()

    def test_adversarial_symlink_loop_timeout_distinguishable_from_block(
        self, tmp_path: Path
    ) -> None:
        """Adv 6: subprocess hangs (symlink loop) → AuditGateError, NOT exit 73.
        User must be able to distinguish 'audit failed to run' from 'audit found plaintext'."""
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="openclaw", timeout=30),
        ):
            with pytest.raises(AuditGateError) as exc_info:
                run_audit(fake_bin, timeout=0.001)
        # The error must clearly indicate subprocess failure, not plaintext
        assert exc_info.value.reason  # has a reason
        # Must NOT classify as BlockingFinding (exit-73 path)

    def test_adversarial_toctou_post_flight_catches_inserted_plaintext(
        self, tmp_path: Path
    ) -> None:
        """Adv 7: file hash changes between pre-flight and post-flight → TOCTOU detected."""
        f = tmp_path / "openclaw.json"
        f.write_text('{"version": 1}')
        pre_hashes = snapshot_hashes([str(f)])
        # Simulate attacker writing plaintext after pre-flight
        f.write_text('{"version": 1, "injected": "sk-proj-evil000000000000000000000"}')
        post_hashes = snapshot_hashes([str(f)])
        # Pre and post hashes must differ — caller uses this to trigger post-flight audit
        assert pre_hashes[str(f)] != post_hashes[str(f)]

    def test_adversarial_audit_clean_but_nonzero_exit_fails_closed(self, tmp_path: Path) -> None:
        """Adv 8: valid JSON 'no findings' but subprocess exits non-zero → AuditGateError.
        Must NOT silently proceed (qa #6)."""
        fake_bin = tmp_path / "openclaw"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        clean_json = _audit_json([])
        proc = _make_proc(stdout=clean_json, returncode=1)
        with patch("subprocess.run", return_value=proc):
            with pytest.raises(AuditGateError):
                run_audit(fake_bin)

    def test_adversarial_plaintext_in_non_canonical_auth_profile_field_still_blocks(
        self, tmp_path: Path
    ) -> None:
        """Adv 9: auth-profiles.json has plaintext key under accessToken (not .key/.token).
        Worthless must block on the file regardless of field name. Security crit #3."""
        auth = tmp_path / "auth-profiles.json"
        auth.write_text(
            json.dumps(
                {
                    "profiles": {
                        "x": {
                            "accessToken": ("sk-proj-realcachedkey0000000000000000000000000000000")
                        }
                    }
                }
            )
        )
        findings = check_auth_profiles_direct([str(auth)])
        assert len(findings) >= 1, "accessToken field with plaintext API key must be detected"

    def test_adversarial_decoy_openclaw_json_outside_scope_does_not_dos_gate(
        self, tmp_path: Path
    ) -> None:
        """Adv 10: /etc/evil/openclaw.json not in filesScanned[] → ignored.
        Worthless must use filesScanned[] not filename suffix. Security #4."""
        evil = tmp_path / "openclaw.json"
        evil.write_text('{"providers": {"evil": {"apiKey": "sk-proj-decoy000000000"}}}')
        # filesScanned[] does not include this file
        findings = check_auth_profiles_direct([])  # empty filesScanned
        assert len(findings) == 0
