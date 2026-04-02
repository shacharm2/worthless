"""Tests for indistinguishable decoy key generation (WOR-31)."""

from __future__ import annotations

import math
import string
from collections import Counter

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


# ---------------------------------------------------------------------------
# Statistical indistinguishability tests (WOR-31 Step 8)
# ---------------------------------------------------------------------------

# Provider prefixes used for generating decoys in statistical tests.
_PROVIDER_PREFIXES: dict[str, str] = {
    "openai": "sk-proj-",
    "anthropic": "sk-ant-api03-",
    "google": "AIzaSy",
    "xai": "xai-",
}


def _extract_random_body(provider: str, decoy: str) -> str:
    """Strip the prefix and literal segments, returning only random chars."""
    prefix = _PROVIDER_PREFIXES[provider]
    body = decoy[len(prefix):]
    fmt = PROVIDER_FORMATS[provider]
    # Remove literal segments, keeping only random portions.
    random_parts: list[str] = []
    offset = 0
    for segment in fmt["segments"]:
        kind = segment[0]
        if kind == "random":
            length = segment[1]
            random_parts.append(body[offset:offset + length])
            offset += length
        elif kind == "literal":
            literal = segment[1]
            offset += len(literal)
    return "".join(random_parts)


def _expected_entropy_uniform(length: int, charset_size: int) -> float:
    """Compute the expected Shannon entropy of a uniform random string.

    For a string of *length* L drawn uniformly from an alphabet of size K,
    the expected entropy is computed via the exact multinomial expectation.
    This accounts for the finite-sample bias where short strings cannot
    achieve the theoretical maximum log2(K).

    Uses the Grassberger/Miller-Madow-corrected approximation:
        H_expected ~ log2(K) - (K - 1) / (2 * L * ln(2))
    which is accurate when L >> 1.
    """
    return math.log2(charset_size) - (charset_size - 1) / (2 * length * math.log(2))


def _chi_square_within_expected_variance(
    stat: float,
    degrees_of_freedom: int,
    sigma: float = 6.0,
) -> bool:
    """Return True when a chi-square statistic stays within a conservative
    number of standard deviations from its null expectation.

    Under the null, chi-square(df) has mean=df and stddev=sqrt(2*df). Using a
    sigma gate is much less flaky than a hard p-value cutoff for randomized
    test fixtures while still catching large distribution shifts.
    """
    mean = degrees_of_freedom
    stddev = math.sqrt(2 * degrees_of_freedom)
    return stat <= mean + sigma * stddev


class TestChiSquareVarianceGate:
    def test_accepts_stat_within_six_sigma(self) -> None:
        degrees_of_freedom = 63
        stat = degrees_of_freedom + 5.5 * math.sqrt(2 * degrees_of_freedom)

        assert _chi_square_within_expected_variance(stat, degrees_of_freedom)

    def test_rejects_stat_beyond_six_sigma(self) -> None:
        degrees_of_freedom = 63
        stat = degrees_of_freedom + 6.5 * math.sqrt(2 * degrees_of_freedom)

        assert not _chi_square_within_expected_variance(stat, degrees_of_freedom)


@pytest.mark.slow
@pytest.mark.timeout(120)
class TestStatisticalIndistinguishability:
    """Prove decoy random portions are statistically uniform.

    These tests use chi-squared goodness-of-fit to verify that character
    frequencies match a uniform distribution over the provider's charset.
    """

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "xai"])
    def test_aggregate_character_frequency(self, provider: str) -> None:
        """Chi-squared on aggregate character frequency across N=1000 decoys.

        Null hypothesis: characters are drawn uniformly from the charset.
        We reject only when the chi-square statistic exceeds a very conservative
        6-sigma bound above the null expectation.
        """
        from scipy.stats import chisquare

        fmt = PROVIDER_FORMATS[provider]
        charset = fmt["charset"]
        n_decoys = 1000

        counter: Counter[str] = Counter()
        for _ in range(n_decoys):
            decoy = make_decoy(provider, _PROVIDER_PREFIXES[provider])
            body = _extract_random_body(provider, decoy)
            counter.update(body)

        # Build observed frequencies in charset order.
        observed = [counter.get(c, 0) for c in charset]
        total = sum(observed)
        expected_freq = total / len(charset)
        expected = [expected_freq] * len(charset)

        stat, _ = chisquare(observed, f_exp=expected)
        degrees_of_freedom = len(charset) - 1
        assert _chi_square_within_expected_variance(stat, degrees_of_freedom), (
            f"{provider}: chi-square stat {stat:.2f} exceeds the 6-sigma "
            f"null bound for df={degrees_of_freedom}"
        )

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "xai"])
    def test_per_position_frequency(self, provider: str) -> None:
        """Per-position chi-squared in 10-char windows to detect positional bias.

        For each window of 10 characters, we run a chi-squared test.
        We assert no window exceeds a conservative 6-sigma null bound.
        """
        from scipy.stats import chisquare

        fmt = PROVIDER_FORMATS[provider]
        charset = fmt["charset"]
        n_decoys = 500

        # Collect all random bodies.
        bodies: list[str] = []
        for _ in range(n_decoys):
            decoy = make_decoy(provider, _PROVIDER_PREFIXES[provider])
            bodies.append(_extract_random_body(provider, decoy))

        body_len = len(bodies[0])
        window_size = 10
        n_windows = body_len // window_size

        for w in range(n_windows):
            start = w * window_size
            end = start + window_size
            counter: Counter[str] = Counter()
            for body in bodies:
                counter.update(body[start:end])

            observed = [counter.get(c, 0) for c in charset]
            total = sum(observed)
            if total == 0:
                continue
            expected_freq = total / len(charset)
            expected = [expected_freq] * len(charset)

            stat, _ = chisquare(observed, f_exp=expected)
            degrees_of_freedom = len(charset) - 1
            assert _chi_square_within_expected_variance(stat, degrees_of_freedom), (
                f"{provider} window [{start}:{end}]: chi-square stat {stat:.2f} "
                f"exceeds the 6-sigma null bound for df={degrees_of_freedom}"
            )

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "xai"])
    def test_entropy_consistency(self, provider: str) -> None:
        """Mean Shannon entropy should be close to the expected value for
        a uniform random string of the same length and charset.

        We compare against the finite-sample expected entropy (not the
        theoretical max log2(K)), which accounts for collision bias in
        short strings. Tolerance is 0.5 bits.
        """
        fmt = PROVIDER_FORMATS[provider]
        charset = fmt["charset"]
        charset_size = len(charset)
        n_decoys = 100

        # Determine random body length from format segments.
        random_body_len = sum(
            seg[1] for seg in fmt["segments"] if seg[0] == "random"
        )
        expected_ent = _expected_entropy_uniform(random_body_len, charset_size)

        entropies: list[float] = []
        for _ in range(n_decoys):
            decoy = make_decoy(provider, _PROVIDER_PREFIXES[provider])
            body = _extract_random_body(provider, decoy)
            # Compute Shannon entropy of the random body.
            length = len(body)
            if length == 0:
                continue
            freq = Counter(body)
            ent = -sum(
                (count / length) * math.log2(count / length)
                for count in freq.values()
            )
            entropies.append(ent)

        mean_entropy = sum(entropies) / len(entropies)
        assert abs(mean_entropy - expected_ent) < 0.5, (
            f"{provider}: mean entropy {mean_entropy:.3f} deviates from "
            f"expected {expected_ent:.3f} by more than 0.5 bits "
            f"(body_len={random_body_len}, charset_size={charset_size})"
        )
