"""Security test suite for the Worthless CLI.

Covers attack vectors from 5 security reviews:
- Path traversal via alias injection
- File permission hardening (0600/0700)
- Key material zeroing (SR-02)
- FK CASCADE integrity
- Fernet key fd-inheritance (no env leak)
- Provider gating (unsupported providers rejected)
- Low-entropy decoy filtering
- Alias validation (alphanumeric, dash, underscore only)
- Atomic .env rewriting (tempfile + os.replace)
- Core dump suppression (RLIMIT_CORE)
"""

from __future__ import annotations

import resource
import sqlite3
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from worthless.cli.bootstrap import ensure_home
from worthless.cli.commands.lock import _enroll_single, _lock_keys
from worthless.cli.decoy import make_decoy
from worthless.cli.dotenv_rewriter import rewrite_env_key, scan_env_keys, shannon_entropy
from worthless.cli.errors import WorthlessError
from worthless.cli.key_patterns import ENTROPY_THRESHOLD
from worthless.cli.process import disable_core_dumps, spawn_proxy
from worthless.cli.scanner import scan_files
from worthless.crypto.splitter import split_key
from worthless.storage.repository import StoredShard


# =====================================================================
# 1-3. PATH TRAVERSAL
# =====================================================================


class TestPathTraversal:
    """Alias injection must not escape the shard_a directory."""

    def test_enroll_rejects_dotdot_fernet_key(self, tmp_path: Path) -> None:
        """enroll --alias '../fernet.key' must be rejected."""
        home = ensure_home(tmp_path / ".worthless")
        with pytest.raises(WorthlessError, match="Invalid alias"):
            _enroll_single("../fernet.key", "sk-test-key-abcdef1234567890", "openai", home)

    def test_enroll_rejects_dotdot_etc_passwd(self, tmp_path: Path) -> None:
        """enroll --alias '../../etc/passwd' must be rejected."""
        home = ensure_home(tmp_path / ".worthless")
        with pytest.raises(WorthlessError, match="Invalid alias"):
            _enroll_single("../../etc/passwd", "sk-test-key-abcdef1234567890", "openai", home)

    def test_enroll_rejects_slash(self, tmp_path: Path) -> None:
        """Aliases containing slashes are rejected."""
        home = ensure_home(tmp_path / ".worthless")
        with pytest.raises(WorthlessError, match="Invalid alias"):
            _enroll_single("foo/bar", "sk-test-key-abcdef1234567890", "openai", home)

    def test_enroll_accepts_valid_alias(self, tmp_path: Path) -> None:
        """A clean alphanumeric alias with dashes/underscores must succeed."""
        home = ensure_home(tmp_path / ".worthless")
        _enroll_single("valid-alias_01", "sk-test-key-abcdef1234567890", "openai", home)
        assert (home.shard_a_dir / "valid-alias_01").exists()


# =====================================================================
# 4-7. FILE PERMISSIONS
# =====================================================================


class TestFilePermissions:
    """All secret files must be created with restrictive permissions."""

    def test_shard_a_file_permissions(self, tmp_path: Path) -> None:
        """shard_a files must be created with 0600."""
        home = ensure_home(tmp_path / ".worthless")
        _enroll_single("perm-test", "sk-test-key-abcdef1234567890", "openai", home)
        shard_path = home.shard_a_dir / "perm-test"
        mode = stat.S_IMODE(shard_path.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_db_file_permissions(self, tmp_path: Path) -> None:
        """DB file must be created with 0600."""
        home = ensure_home(tmp_path / ".worthless")
        mode = stat.S_IMODE(home.db_path.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_fernet_key_permissions(self, tmp_path: Path) -> None:
        """fernet.key must be created with 0600."""
        home = ensure_home(tmp_path / ".worthless")
        mode = stat.S_IMODE(home.fernet_key_path.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_base_dir_permissions(self, tmp_path: Path) -> None:
        """base_dir must be created with 0700."""
        home = ensure_home(tmp_path / ".worthless")
        mode = stat.S_IMODE(home.base_dir.stat().st_mode)
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"

    def test_shard_a_dir_permissions(self, tmp_path: Path) -> None:
        """shard_a directory must be created with 0700."""
        home = ensure_home(tmp_path / ".worthless")
        mode = stat.S_IMODE(home.shard_a_dir.stat().st_mode)
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"


# =====================================================================
# 8-9. KEY ZEROING (SR-02)
# =====================================================================


class TestKeyZeroing:
    """Cryptographic material must be zeroable in-place."""

    def test_split_result_zero_clears_all_fields(self) -> None:
        """SplitResult.zero() must set all buffers to all-zero bytes."""
        sr = split_key(b"sk-test-key-abcdef1234567890")
        # Verify fields are non-zero before zeroing
        assert any(b != 0 for b in sr.shard_a)
        assert any(b != 0 for b in sr.shard_b)

        sr.zero()

        assert all(b == 0 for b in sr.shard_a), "shard_a not zeroed"
        assert all(b == 0 for b in sr.shard_b), "shard_b not zeroed"
        assert all(b == 0 for b in sr.commitment), "commitment not zeroed"
        assert all(b == 0 for b in sr.nonce), "nonce not zeroed"

    def test_split_result_zero_preserves_length(self) -> None:
        """Zeroing must not change buffer lengths."""
        key = b"sk-test-key-abcdef1234567890"
        sr = split_key(key)
        lengths = (len(sr.shard_a), len(sr.shard_b), len(sr.commitment), len(sr.nonce))
        sr.zero()
        assert (len(sr.shard_a), len(sr.shard_b), len(sr.commitment), len(sr.nonce)) == lengths

    def test_split_result_zero_idempotent(self) -> None:
        """Calling zero() twice must not raise."""
        sr = split_key(b"sk-test-key-abcdef1234567890")
        sr.zero()
        sr.zero()

    def test_stored_shard_zero_clears_all_fields(self) -> None:
        """StoredShard.zero() must set shard_b, commitment, nonce to zeros."""
        shard = StoredShard(
            shard_b=bytearray(b"\xde\xad\xbe\xef" * 8),
            commitment=bytearray(b"\xca\xfe" * 16),
            nonce=bytearray(b"\x01\x02\x03\x04" * 8),
            provider="openai",
        )
        shard.zero()
        assert all(b == 0 for b in shard.shard_b), "shard_b not zeroed"
        assert all(b == 0 for b in shard.commitment), "commitment not zeroed"
        assert all(b == 0 for b in shard.nonce), "nonce not zeroed"

    def test_split_result_repr_redacted(self) -> None:
        """repr/str must never leak key material."""
        sr = split_key(b"sk-test-key-abcdef1234567890")
        text = repr(sr)
        assert "redacted" in text
        assert sr.shard_a.hex() not in text


# =====================================================================
# 10. FK CASCADE
# =====================================================================


class TestFKCascade:
    """Deleting a shard must cascade to enrollments."""

    def test_delete_shard_cascades_to_enrollments(self, tmp_path: Path) -> None:
        """Enrollment rows are deleted when the parent shard is deleted."""
        home = ensure_home(tmp_path / ".worthless")
        _enroll_single("cascade-test", "sk-test-key-abcdef1234567890", "openai", home)

        conn = sqlite3.connect(str(home.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        rows = conn.execute(
            "SELECT * FROM enrollments WHERE key_alias = ?", ("cascade-test",)
        ).fetchall()
        assert len(rows) == 1

        conn.execute("DELETE FROM shards WHERE key_alias = ?", ("cascade-test",))
        conn.commit()

        rows = conn.execute(
            "SELECT * FROM enrollments WHERE key_alias = ?", ("cascade-test",)
        ).fetchall()
        assert len(rows) == 0
        conn.close()


# =====================================================================
# 11. FERNET KEY NOT IN ENV
# =====================================================================


class TestFernetKeyNotInEnv:
    """spawn_proxy must not put WORTHLESS_FERNET_KEY in subprocess env."""

    def test_fernet_key_removed_from_env_dict(self) -> None:
        """After spawn_proxy processes the env dict, WORTHLESS_FERNET_KEY must be popped."""
        env = {
            "WORTHLESS_FERNET_KEY": Fernet.generate_key().decode(),
            "WORTHLESS_DB_PATH": "/tmp/fake.db",  # noqa: S108
        }
        env_copy = dict(env)
        captured_env = {}

        def fake_popen(cmd, *, env=None, **kwargs):
            captured_env.update(env or {})
            raise RuntimeError("abort spawn")

        with patch("worthless.cli.process.subprocess.Popen", side_effect=fake_popen):
            with pytest.raises(RuntimeError, match="abort spawn"):
                spawn_proxy(env_copy, port=9999)

        assert "WORTHLESS_FERNET_KEY" not in captured_env, (
            "Fernet key must not appear in subprocess environment"
        )

    def test_fernet_fd_is_set_when_key_provided(self) -> None:
        """WORTHLESS_FERNET_FD must be set in env when a Fernet key is provided."""
        env = {
            "WORTHLESS_FERNET_KEY": Fernet.generate_key().decode(),
            "WORTHLESS_DB_PATH": "/tmp/fake.db",  # noqa: S108
        }
        captured_env = {}

        def fake_popen(cmd, *, env=None, **kwargs):
            captured_env.update(env or {})
            raise RuntimeError("abort spawn")

        with patch("worthless.cli.process.subprocess.Popen", side_effect=fake_popen):
            with pytest.raises(RuntimeError, match="abort spawn"):
                spawn_proxy(dict(env), port=9999)

        assert "WORTHLESS_FERNET_FD" in captured_env, (
            "WORTHLESS_FERNET_FD must be set for fd inheritance"
        )


# =====================================================================
# 12. PROVIDER GATING
# =====================================================================


class TestProviderGating:
    """Lock must refuse to enroll unsupported providers."""

    def test_lock_skips_google_provider(self, tmp_path: Path) -> None:
        """Google keys are detected but skipped (not supported for proxy redirect)."""
        home = ensure_home(tmp_path / ".worthless")
        env_file = tmp_path / ".env"
        env_file.write_text("GOOGLE_KEY=AIzaSyA3x7bK9mQ2rT4vU5wE1dF6gH8jL0pN2sR\n")

        count = _lock_keys(env_file, home)
        assert count == 0, "Google provider should be skipped"

    def test_lock_skips_xai_provider(self, tmp_path: Path) -> None:
        """xai keys are detected but skipped (not supported for proxy redirect)."""
        home = ensure_home(tmp_path / ".worthless")
        env_file = tmp_path / ".env"
        env_file.write_text("XAI_KEY=xai-a3x7bK9mQ2rT4vU5wE1dF6gH8jL0pN2sR\n")

        count = _lock_keys(env_file, home)
        assert count == 0, "xai provider should be skipped"

    def test_lock_accepts_openai_provider(self, tmp_path: Path) -> None:
        """OpenAI keys should be enrolled successfully."""
        home = ensure_home(tmp_path / ".worthless")
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-proj-a3x7bK9mQ2rT4vU5wE1dF6gH8jL0pN2sR\n")

        count = _lock_keys(env_file, home)
        assert count == 1, "OpenAI provider should be accepted"

    def test_lock_accepts_anthropic_provider(self, tmp_path: Path) -> None:
        """Anthropic keys should be enrolled successfully."""
        home = ensure_home(tmp_path / ".worthless")
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-ant-api03-a3x7bK9mQ2rT4vU5wE1dF6gH8jL0pN2sR\n")

        count = _lock_keys(env_file, home)
        assert count == 1, "Anthropic provider should be accepted"


# =====================================================================
# 13. ENTROPY FILTERING
# =====================================================================


class TestEntropyFiltering:
    """High-entropy decoys must be filtered by the hash registry, not entropy."""

    def test_decoy_has_high_entropy(self) -> None:
        """Decoys produced by make_decoy must have entropy ABOVE threshold (WOR-31)."""
        decoy = make_decoy("openai", "sk-proj-")
        entropy = shannon_entropy(decoy)
        assert entropy > ENTROPY_THRESHOLD, (
            f"Decoy entropy {entropy:.2f} should be above threshold {ENTROPY_THRESHOLD}"
        )

    def test_decoy_not_detected_by_scan_env_keys_with_predicate(self, tmp_path: Path) -> None:
        """scan_env_keys must skip decoy values when is_decoy predicate is provided."""
        decoy = make_decoy("openai", "sk-proj-")

        env_file = tmp_path / ".env"
        env_file.write_text(f"OPENAI_API_KEY={decoy}\n")

        # Without predicate, decoy IS detected (it's high entropy now)
        results_no_pred = scan_env_keys(env_file)
        assert len(results_no_pred) == 1, "High-entropy decoy should be detected without predicate"

        # With predicate, decoy is filtered
        results_with_pred = scan_env_keys(env_file, is_decoy=lambda v: v == decoy)
        assert len(results_with_pred) == 0, "Decoy should be filtered by is_decoy predicate"

    def test_decoy_detected_by_scanner_without_registry(self, tmp_path: Path) -> None:
        """scan_files flags decoy values (they're high-entropy now — registry needed to filter)."""
        decoy = make_decoy("openai", "sk-proj-")

        env_file = tmp_path / ".env"
        env_file.write_text(f"OPENAI_API_KEY={decoy}\n")

        findings = scan_files([env_file])
        assert len(findings) == 1, "High-entropy decoy should be detected by scanner"

    def test_real_key_detected_by_scanner(self, tmp_path: Path) -> None:
        """Real high-entropy keys must be detected (control test)."""
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-proj-a3x7bK9mQ2rT4vU5wE1dF6gH8jL0pN2sR\n")

        findings = scan_files([env_file])
        assert len(findings) == 1, "Real key should be detected"


# =====================================================================
# 14. ALIAS VALIDATION
# =====================================================================


class TestAliasValidation:
    """Aliases must only contain alphanumeric, dash, underscore."""

    @pytest.mark.parametrize(
        "bad_alias",
        [
            "../escape",
            "foo/bar",
            "hello world",
            "semi;colon",
            "back`tick",
            "dollar$sign",
            "new\nline",
            "tab\there",
            "pipe|char",
            "angle<bracket",
            "ampersand&",
            "at@sign",
            "dot.name",
        ],
    )
    def test_rejects_invalid_aliases(self, bad_alias: str, tmp_path: Path) -> None:
        home = ensure_home(tmp_path / ".worthless")
        with pytest.raises(WorthlessError, match="Invalid alias"):
            _enroll_single(bad_alias, "sk-test-key-abcdef1234567890", "openai", home)

    @pytest.mark.parametrize(
        "good_alias",
        [
            "my-key",
            "key_123",
            "OPENAI-abc123",
            "a",
            "A1-b2_c3",
        ],
    )
    def test_accepts_valid_aliases(self, good_alias: str, tmp_path: Path) -> None:
        home = ensure_home(tmp_path / ".worthless")
        _enroll_single(good_alias, "sk-test-key-abcdef1234567890", "openai", home)
        assert (home.shard_a_dir / good_alias).exists()


# =====================================================================
# 15. ATOMIC WRITES
# =====================================================================


class TestAtomicWrites:
    """dotenv rewriter must use tempfile + os.replace, not direct write."""

    def test_rewrite_is_atomic(self, tmp_path: Path) -> None:
        """Verify rewrite doesn't corrupt the file on success.

        python-dotenv's set_key uses an atomic rewrite context manager
        (temp file + os.replace). We verify the outcome: inode changes
        (proving a new file was swapped in) and content is correct.
        """
        env_file = tmp_path / ".env"
        env_file.write_text("MY_KEY=old_value\n")
        inode_before = env_file.stat().st_ino

        rewrite_env_key(env_file, "MY_KEY", "new_value")

        content = env_file.read_text()
        assert "new_value" in content
        # Atomic replace creates a new inode on most filesystems
        inode_after = env_file.stat().st_ino
        assert inode_before != inode_after, "Atomic write should create a new file (new inode)"

    def test_rewrite_preserves_content_on_success(self, tmp_path: Path) -> None:
        """After rewrite, file must have the new value and other lines intact."""
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nFOO=bar\nMY_KEY=old_value\nBAZ=qux\n")

        rewrite_env_key(env_file, "MY_KEY", "new_value")

        content = env_file.read_text()
        assert "MY_KEY=new_value" in content
        assert "FOO=bar" in content
        assert "BAZ=qux" in content
        assert "# comment" in content

    def test_rewrite_raises_on_missing_var(self, tmp_path: Path) -> None:
        """KeyError must be raised if the variable is not found."""
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")

        with pytest.raises(KeyError, match="NONEXISTENT"):
            rewrite_env_key(env_file, "NONEXISTENT", "value")


# =====================================================================
# 16. CORE DUMP SUPPRESSION
# =====================================================================


class TestCoreDumpSuppression:
    """disable_core_dumps must set RLIMIT_CORE to 0."""

    def test_disable_core_dumps_sets_rlimit(self) -> None:
        """After calling disable_core_dumps, RLIMIT_CORE soft and hard must be 0."""
        disable_core_dumps()
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        assert soft == 0, f"RLIMIT_CORE soft limit should be 0, got {soft}"
        assert hard == 0, f"RLIMIT_CORE hard limit should be 0, got {hard}"

    def test_disable_core_dumps_idempotent(self) -> None:
        """Calling disable_core_dumps twice must not raise."""
        disable_core_dumps()
        disable_core_dumps()
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        assert soft == 0
