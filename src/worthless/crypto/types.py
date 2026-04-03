"""Cryptographic data types for the Worthless key-splitting system."""

from __future__ import annotations

from dataclasses import dataclass


def zero_buf(buf: bytearray) -> None:
    """Zero a bytearray in-place (SR-02).

    Uses ``bytearray(n)`` which maps to a single ``calloc``/``memset`` in
    CPython — no temporary ``bytes`` object allocated.  All zeroing across
    the crypto module funnels through this function so the strategy can be
    upgraded to ``ctypes.memset`` or Rust FFI in one place.
    """
    buf[:] = bytearray(len(buf))


@dataclass(frozen=True, slots=True)
class SplitResult:
    """Result of splitting an API key into two XOR shards with HMAC commitment.

    All byte fields use ``bytearray`` (SR-01) so they can be zeroed after use.
    The dataclass is frozen to prevent accidental field reassignment, but the
    bytearray contents remain mutable for in-place zeroing via :meth:`zero`.

    All fields are redacted in ``__repr__`` and ``__str__`` to prevent
    accidental logging of key material (SR-04).
    """

    shard_a: bytearray
    shard_b: bytearray
    commitment: bytearray
    nonce: bytearray

    def zero(self) -> None:
        """Zero all secret material in-place (SR-02).

        Safe to call multiple times.  Works despite ``frozen=True`` because
        we mutate bytearray *contents*, not the field references.

        Commitment and nonce are not secret, but are zeroed as
        defense-in-depth — minimises residual crypto context in memory.
        """
        for buf in (self.shard_a, self.shard_b, self.commitment, self.nonce):
            zero_buf(buf)

    def __repr__(self) -> str:
        return (
            "SplitResult("
            "shard_a=<redacted>, "
            "shard_b=<redacted>, "
            "commitment=<redacted>, "
            "nonce=<redacted>)"
        )

    def __str__(self) -> str:
        return self.__repr__()
