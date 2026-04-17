"""Live integration test — prove split/reconstruct works against real OpenAI.

No Docker, no mocks. Splits a real API key using format-preserving split,
proves shard-A is useless alone, reconstructs, and gets a real completion.

Requires OPENAI_API_KEY in environment. Costs ~$0.001 per run.

Run with:
    uv run pytest tests/test_openclaw_live.py -x -v -m live -o "addopts="
"""

from __future__ import annotations

import os

import httpx
import pytest

from worthless.cli.key_patterns import detect_prefix
from worthless.crypto.splitter import reconstruct_key_fp, split_key_fp

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not OPENAI_KEY, reason="OPENAI_API_KEY not set"),
    pytest.mark.timeout(30),
]

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
BODY = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
    "max_tokens": 5,
}


def _redact(key: str) -> str:
    return f"{key[:10]}...{key[-4:]}"


def _post(url: str, key: str) -> httpx.Response:
    return httpx.post(
        url,
        json=BODY,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )


class TestLiveReconstruction:
    """Prove format-preserving split/reconstruct against real OpenAI API."""

    def test_live_full_flow(self):
        """The money test: split → shard-A fails → reconstruct → success."""
        print()

        # 1. Format-preserving split
        prefix = detect_prefix(OPENAI_KEY, "openai")
        sr = split_key_fp(OPENAI_KEY, prefix, "openai")
        shard_a = sr.shard_a.decode("utf-8")
        print(f"1. Real key: {_redact(OPENAI_KEY)}")
        print("   Format-preserving split into shard_a + shard_b")
        print(f"   shard_a: {_redact(shard_a)} (looks like a real key!)")
        print(f"   shard_b: {sr.shard_b[:20]}... (charset body)")
        print()

        # 2. Try shard-A against OpenAI → 401
        print("2. Try shard-A as API key against OpenAI:")
        print(f"   Authorization: Bearer {_redact(shard_a)}")
        resp_shard = _post(OPENAI_URL, shard_a)
        print(f"   Response: {resp_shard.status_code}")
        assert resp_shard.status_code == 401, f"Expected 401, got {resp_shard.status_code}"
        print("   shard-A alone is useless (despite looking real)")
        print()

        # 3. Try a random decoy → 401
        fake_decoy = f"sk-proj-{'x' * 40}"
        print("3. Try decoy key against OpenAI:")
        print(f"   Authorization: Bearer {_redact(fake_decoy)}")
        resp_decoy = _post(OPENAI_URL, fake_decoy)
        print(f"   Response: {resp_decoy.status_code}")
        assert resp_decoy.status_code == 401, f"Expected 401, got {resp_decoy.status_code}"
        print("   Decoy is useless")
        print()

        # 4. Reconstruct and call OpenAI → 200
        key_buf = reconstruct_key_fp(
            sr.shard_a,
            sr.shard_b,
            sr.commitment,
            sr.nonce,
            sr.prefix,
            sr.charset,
        )
        reconstructed = key_buf.decode()
        print("4. Reconstruct: shard_a + shard_b (modular arithmetic)")
        print(f"   Reconstructed: {_redact(reconstructed)}")
        assert reconstructed == OPENAI_KEY, "Reconstruction mismatch"

        resp_real = _post(OPENAI_URL, reconstructed)
        print(f"   Response: {resp_real.status_code}")
        # 200 = success, 429 = key recognized but quota exhausted (still proves auth)
        assert resp_real.status_code in (200, 429), (
            f"Expected 200/429, got {resp_real.status_code}: {resp_real.text}"
        )
        if resp_real.status_code == 200:
            content = resp_real.json()["choices"][0]["message"]["content"]
            print(f'   Completion: "{content.strip()}"')
        else:
            content = "(quota exhausted — but key was recognized)"
            print(f"   {content}")
        print()

        # 5. Zero key material
        key_buf[:] = b"\x00" * len(key_buf)
        sr.zero()

        print("5. Key material zeroed from memory")
        print()
        print("   shard-A alone?  401 (format-preserving but not the real key)")
        print("   decoy alone?    401")
        print(f'   reconstructed?  {resp_real.status_code} — "{content.strip()}"')
        print()
        print("   PASS")
