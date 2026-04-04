import json
import pytest
from worthless.crypto.splitter import split_key, reconstruct_key

@pytest.mark.benchmark(group="crypto")
def test_bench_reconstruct_key(benchmark):
    """Measures pure Python bitwise operations + HMAC verification overhead."""
    key = b"sk-ant-api03-abc123def456ghi789jkl012mno345pqr678stu901vwx234"
    sr = split_key(key)

    def run_reconstruct():
        return reconstruct_key(sr.shard_a, sr.shard_b, sr.commitment, sr.nonce)

    # Benchmark runs this function thousands of times automatically
    benchmark(run_reconstruct)

@pytest.mark.benchmark(group="json")
def test_bench_json_serialize(benchmark):
    """Measures standard library JSON serialization overhead."""
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."}
            ] + [{"role": "user", "content": "Measure this!"}] * 50,
        "temperature": 0.5,
        "max_tokens": 8192,
        "stream": True,
    }

    def run_json():
        return json.dumps(payload).encode('utf-8')

    benchmark(run_json)
