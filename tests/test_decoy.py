"""Tests for indistinguishable decoy key generation (WOR-31)."""

from __future__ import annotations

import re
import string

import pytest

from worthless.cli.decoy import PROVIDER_FORMATS, make_decoy
from worthless.cli.dotenv_rewriter import shannon_entropy


# ---------------------------------------------------------------------------
# Format correctness tests
# ---------------------------------------------------------------------------

BASE64URL = string.ascii_letters + string.digits + "_-"
ALPHANUMERIC = string.ascii_letters + string.digits


class TestOpenAIDecoy:
    def test_prefix(self):
        decoy = make_decoy("openai", "sk-proj-")
        assert decoy.startswith("sk-proj-")

    def test_length(self):
        decoy = make_decoy("openai", "sk-proj-")
        assert len(decoy) == 164

    def test_contains_marker(self):
        decoy = make_decoy("openai", "sk-proj-")
        assert "T3BlbkFJ" in decoy

    def test_marker_position(self):
        """T3BlbkFJ should appear after prefix + 74 random chars."""
        decoy = make_decoy("openai", "sk-proj-")
        marker_pos = decoy.index("T3BlbkFJ")
        assert marker_pos == 8 + 74  # len("sk-proj-") + 74

    def test_charset(self):
        decoy = make_decoy("openai", "sk-proj-")
        # Strip prefix and marker, check remaining chars
        body = decoy[len("sk-proj-"):]
        body = body.replace("T3BlbkFJ", "")
        assert all(c in BASE64URL for c in body)

    def test_high_entropy(self):
        decoy = make_decoy("openai", "sk-proj-")
        assert shannon_entropy(decoy) > 4.5


class TestAnthropicDecoy:
    def test_prefix(self):
        decoy = make_decoy("anthropic", "sk-ant-api03-")
        assert decoy.startswith("sk-ant-api03-")

    def test_length(self):
        decoy = make_decoy("anthropic", "sk-ant-api03-")
        assert len(decoy) == 108

    def test_ends_with_aa(self):
        decoy = make_decoy("anthropic", "sk-ant-api03-")
        assert decoy.endswith("AA")

    def test_charset(self):
        decoy = make_decoy("anthropic", "sk-ant-api03-")
        body = decoy[len("sk-ant-api03-"):-2]  # strip prefix and AA suffix
        assert all(c in BASE64URL for c in body)

    def test_high_entropy(self):
        decoy = make_decoy("anthropic", "sk-ant-api03-")
        assert shannon_entropy(decoy) > 4.5


class TestGoogleDecoy:
    def test_prefix(self):
        decoy = make_decoy("google", "AIzaSy")
        assert decoy.startswith("AIzaSy")

    def test_length(self):
        decoy = make_decoy("google", "AIzaSy")
        assert len(decoy) == 39

    def test_charset(self):
        decoy = make_decoy("google", "AIzaSy")
        body = decoy[len("AIzaSy"):]
        assert all(c in BASE64URL for c in body)

    def test_high_entropy(self):
        decoy = make_decoy("google", "AIzaSy")
        assert shannon_entropy(decoy) > 4.5


class TestXaiDecoy:
    def test_prefix(self):
        decoy = make_decoy("xai", "xai-")
        assert decoy.startswith("xai-")

    def test_length(self):
        decoy = make_decoy("xai", "xai-")
        assert len(decoy) == 84

    def test_charset(self):
        """xAI uses plain alphanumeric, no underscores or hyphens."""
        decoy = make_decoy("xai", "xai-")
        body = decoy[len("xai-"):]
        assert all(c in ALPHANUMERIC for c in body)

    def test_high_entropy(self):
        decoy = make_decoy("xai", "xai-")
        assert shannon_entropy(decoy) > 4.5


# ---------------------------------------------------------------------------
# Cross-provider tests
# ---------------------------------------------------------------------------


class TestDecoyGeneral:
    @pytest.mark.parametrize("provider,prefix", [
        ("openai", "sk-proj-"),
        ("anthropic", "sk-ant-api03-"),
        ("google", "AIzaSy"),
        ("xai", "xai-"),
    ])
    def test_two_calls_produce_different_values(self, provider, prefix):
        """CSPRNG should produce unique decoys."""
        d1 = make_decoy(provider, prefix)
        d2 = make_decoy(provider, prefix)
        assert d1 != d2

    def test_unknown_provider_uses_prefix_and_base62(self):
        """Fallback for unknown providers: keep prefix, fill with base62."""
        decoy = make_decoy("unknown", "custom-prefix-")
        assert decoy.startswith("custom-prefix-")
        body = decoy[len("custom-prefix-"):]
        assert len(body) == 40  # default fallback length
        assert all(c in ALPHANUMERIC for c in body)

    def test_provider_formats_has_all_known_providers(self):
        assert "openai" in PROVIDER_FORMATS
        assert "anthropic" in PROVIDER_FORMATS
        assert "google" in PROVIDER_FORMATS
        assert "xai" in PROVIDER_FORMATS

    @pytest.mark.parametrize("provider,prefix", [
        ("openai", "sk-proj-"),
        ("anthropic", "sk-ant-api03-"),
        ("google", "AIzaSy"),
        ("xai", "xai-"),
    ])
    def test_decoy_matches_key_pattern_regex(self, provider, prefix):
        """Decoys should be detected by the same KEY_PATTERN used for real keys."""
        from worthless.cli.key_patterns import KEY_PATTERN
        decoy = make_decoy(provider, prefix)
        assert KEY_PATTERN.search(decoy) is not None
