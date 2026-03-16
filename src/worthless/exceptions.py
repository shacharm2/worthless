"""Worthless exception hierarchy."""


class ShardTamperedError(Exception):
    """Raised when HMAC verification fails during reconstruction."""
