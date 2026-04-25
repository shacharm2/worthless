"""Worthless cryptographic primitives.

Reconstruction primitives (``reconstruct_key``, ``secure_key``) live in
:mod:`worthless.crypto.reconstruction` so the proxy can import them
without tripping the AST CI guard at
``tests/architecture/test_proxy_imports.py``. Enrollment-side
``split_key`` lives in :mod:`worthless.crypto.splitter`.
"""

from worthless.crypto.reconstruction import reconstruct_key, secure_key
from worthless.crypto.splitter import split_key
from worthless.crypto.types import SplitResult
from worthless.exceptions import ShardTamperedError

__all__ = [
    "ShardTamperedError",
    "SplitResult",
    "reconstruct_key",
    "secure_key",
    "split_key",
]
