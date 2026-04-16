"""Live integration test — prove split/reconstruct works against real OpenAI.

No Docker, no mocks. Splits a real API key, proves the halves are useless
alone, reconstructs, and gets a real completion from OpenAI.

Requires OPENAI_API_KEY in environment. Costs ~$0.001 per run.

Run with:
    uv run pytest tests/test_openclaw_live.py -x -v -m live -o "addopts="
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest

from worthless.crypto.splitter import reconstruct_key, split_key

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
    """Prove split/reconstruct against real OpenAI API."""

    def test_live_full_flow(self):
        """The money test: split → halves fail → reconstruct → success."""
        print()

        # 1. Split
        sr = split_key(OPENAI_KEY.encode())
        shard_a_b64 = base64.b64encode(bytes(sr.shard_a)).decode()
        print(f"1. Real key: {_redact(OPENAI_KEY)}")
        print("   Split into shard_a + shard_b")
        print(f"   shard_a: {sr.shard_a.hex()[:20]}... (random bytes)")
        print(f"   shard_b: {sr.shard_b.hex()[:20]}... (random bytes)")
        print()

        # 2. Try shard-A as bearer token → 401
        print("2. Try shard-A as API key against OpenAI:")
        print(f"   Authorization: Bearer {shard_a_b64[:16]}...")
        resp_shard = _post(OPENAI_URL, shard_a_b64)
        print(f"   Response: {resp_shard.status_code}")
        assert resp_shard.status_code == 401, f"Expected 401, got {resp_shard.status_code}"
        print("   shard-A alone is useless")
        print()

        # 3. Try a random decoy → 401
        decoy = base64.b64encode(os.urandom(32)).decode()
        fake_decoy = f"sk-proj-{decoy[:48]}"
        print("3. Try decoy key against OpenAI:")
        print(f"   Authorization: Bearer {_redact(fake_decoy)}")
        resp_decoy = _post(OPENAI_URL, fake_decoy)
        print(f"   Response: {resp_decoy.status_code}")
        assert resp_decoy.status_code == 401, f"Expected 401, got {resp_decoy.status_code}"
        print("   Decoy is useless")
        print()

        # 4. Reconstruct and call OpenAI → 200
        key_buf = reconstruct_key(
            sr.shard_a,
            sr.shard_b,
            sr.commitment,
            sr.nonce,
        )
        reconstructed = key_buf.decode()
        print("4. Reconstruct: shard_a XOR shard_b")
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
        print("   shard-A alone?  401")
        print("   decoy alone?    401")
        print(f'   reconstructed?  200 — "{content.strip()}"')
        print()
        print("   PASS")
