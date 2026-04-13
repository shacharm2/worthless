"""Streaming metering tests — StreamingUsageCollector must extract usage
from SSE chunks without buffering entire responses.

RED tests: these should FAIL because StreamingUsageCollector does not exist yet.
"""

from __future__ import annotations


class TestStreamingUsageCollectorOpenAI:
    """StreamingUsageCollector extracts token counts from OpenAI SSE streams."""

    def test_extracts_usage_from_final_chunk(self):
        """Feed OpenAI-format SSE chunks; collector extracts input/output tokens."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")

        # Typical OpenAI streaming chunks
        chunks = [
            b'data: {"id":"chatcmpl-1","choices":[{"delta":{"role":"assistant"}}]}\n\n',
            b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":" world"}}]}\n\n',
            # Final chunk with usage (stream_options.include_usage=true)
            (
                b'data: {"id":"chatcmpl-1","choices":[],'
                b'"usage":{"prompt_tokens":10,"completion_tokens":5,'
                b'"total_tokens":15},"model":"gpt-4"}\n\n'
            ),
            b"data: [DONE]\n\n",
        ]

        for chunk in chunks:
            collector.feed(chunk)

        usage = collector.result()
        assert usage is not None
        assert usage.total_tokens == 15
        assert usage.model == "gpt-4"

    def test_returns_none_when_no_usage(self):
        """If stream never includes usage data, result() returns None."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")

        chunks = [
            b'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hi"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        for chunk in chunks:
            collector.feed(chunk)

        assert collector.result() is None


class TestStreamingUsageCollectorNoBuf:
    """StreamingUsageCollector must NOT buffer all chunks in memory."""

    def test_no_buffer_growth(self):
        """After feeding 1000 chunks, collector must not store them all.

        The collector should only track the latest usage-bearing data,
        not accumulate a list of every chunk seen.
        """
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")

        # Feed 1000 content chunks (no usage data)
        for i in range(1000):
            chunk = (
                f'data: {{"id":"chatcmpl-1","choices":[{{"delta":{{"content":"word{i}"}}}}]}}\n\n'
            )
            collector.feed(chunk.encode())

        # The collector must NOT have a growing list of all chunks.
        # Check common buffer attribute names.
        for attr_name in ("_chunks", "chunks", "_buffer", "buffer", "collected_chunks"):
            attr = getattr(collector, attr_name, None)
            if attr is not None and isinstance(attr, list | bytearray | bytes):
                assert len(attr) < 100, (
                    f"Collector.{attr_name} has {len(attr)} entries after 1000 chunks — "
                    "it is buffering the entire stream"
                )

        # Also check total object size stays bounded (< 100KB for 1000 chunks)
        import sys

        size = sys.getsizeof(collector)
        # getsizeof is shallow, but a list of 1000 byte-strings would show up
        assert size < 100_000, (
            f"Collector object size is {size} bytes after 1000 chunks — "
            "likely buffering the entire stream"
        )


class TestStreamingUsageCollectorAnthropic:
    """StreamingUsageCollector extracts token counts from Anthropic SSE streams."""

    def test_extracts_usage_from_anthropic_events(self):
        """Feed Anthropic-format SSE events; collector extracts input/output tokens."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="anthropic")

        chunks = [
            (
                b"event: message_start\n"
                b'data: {"type":"message_start","message":'
                b'{"id":"msg_1","model":"claude-3-5-sonnet-20241022",'
                b'"usage":{"input_tokens":25}}}\n\n'
            ),
            (
                b"event: content_block_delta\n"
                b'data: {"type":"content_block_delta",'
                b'"delta":{"type":"text_delta","text":"Hello"}}\n\n'
            ),
            (
                b"event: message_delta\n"
                b'data: {"type":"message_delta",'
                b'"delta":{},"usage":{"output_tokens":10}}\n\n'
            ),
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        for chunk in chunks:
            collector.feed(chunk)

        usage = collector.result()
        assert usage is not None
        assert usage.total_tokens == 35  # 25 input + 10 output
        assert usage.model == "claude-3-5-sonnet-20241022"


class TestStreamingUsageCollectorEdgeCases:
    """Edge cases: split chunks, malformed JSON, empty streams, partial cap."""

    def test_sse_line_split_across_chunks(self):
        """A single SSE data line split between two feed() calls."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")

        full_line = (
            b'data: {"id":"x","choices":[],'
            b'"usage":{"prompt_tokens":5,"completion_tokens":6,'
            b'"total_tokens":11},"model":"gpt-4o"}\n\n'
        )
        mid = len(full_line) // 2
        collector.feed(full_line[:mid])
        collector.feed(full_line[mid:])

        usage = collector.result()
        assert usage is not None
        assert usage.total_tokens == 11
        assert usage.model == "gpt-4o"

    def test_malformed_json_returns_none(self):
        """Garbage JSON in data lines degrades gracefully to None."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")
        collector.feed(b"data: {not valid json}\n\n")
        collector.feed(b"data: [DONE]\n\n")
        assert collector.result() is None

    def test_empty_stream_returns_none(self):
        """No feed() calls at all — result() returns None."""
        from worthless.proxy.metering import StreamingUsageCollector

        assert StreamingUsageCollector(provider="openai").result() is None
        assert StreamingUsageCollector(provider="anthropic").result() is None

    def test_partial_line_cap_prevents_oom(self):
        """100KB without a newline gets capped, then normal data still works."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")

        # Feed 100KB of garbage with no newline
        collector.feed(b"x" * 100_000)
        assert len(collector._partial_line) <= 65_536

        # Feed normal usage data — collector still works
        collector.feed(
            b'\ndata: {"id":"x","choices":[],"usage":{"total_tokens":7},"model":"gpt-4o"}\n\n'
        )
        usage = collector.result()
        assert usage is not None
        assert usage.total_tokens == 7

    def test_partial_line_flushed_on_result(self):
        """Usage data stuck in _partial_line (no trailing newline) must be
        parsed when result() is called."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")

        # Feed a chunk that ends WITHOUT a trailing newline — usage sits in _partial_line
        collector.feed(
            b'data: {"id":"x","choices":[],'
            b'"usage":{"prompt_tokens":5,"completion_tokens":3,'
            b'"total_tokens":8},"model":"gpt-4o"}'
        )
        # _partial_line holds the usage data; no newline means it was never parsed
        assert collector._partial_line != ""

        usage = collector.result()
        assert usage is not None, "result() must flush _partial_line before returning"
        assert usage.total_tokens == 8
        assert usage.model == "gpt-4o"

    def test_non_dict_usage_does_not_crash(self):
        """data: {"usage": 1} and data: {"message": []} must not crash."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="openai")
        collector.feed(b'data: {"usage": 1}\n\n')
        assert collector.result() is None

        collector2 = StreamingUsageCollector(provider="anthropic")
        collector2.feed(b"event: message_start\n")
        collector2.feed(b'data: {"message": []}\n\n')
        assert collector2.result() is None

    def test_non_dict_message_in_anthropic_does_not_crash(self):
        """Anthropic message_start with non-dict message field must not crash."""
        from worthless.proxy.metering import StreamingUsageCollector

        collector = StreamingUsageCollector(provider="anthropic")
        collector.feed(b'event: message_start\ndata: {"message": "string-not-dict"}\n\n')
        assert collector.result() is None
