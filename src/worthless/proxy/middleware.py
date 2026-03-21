"""Proxy middleware — body size limits, security headers.

Middleware executes in reverse registration order in Starlette.
Register body size AFTER CORS so body size checks run first.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds max_bytes (M-1).

    Checks the Content-Length header before the request body is read.
    Returns 413 with a JSON error body matching the provider error format.
    Requests without Content-Length pass through (streaming uploads).
    """

    def __init__(self, app: object, max_bytes: int = 10 * 1024 * 1024) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_bytes = max_bytes

    _BODY_TOO_LARGE = (
        b'{"error": {"message": "request too large",'
        b' "type": "invalid_request_error",'
        b' "param": null, "code": null}}'
    )

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    return Response(
                        content=self._BODY_TOO_LARGE,
                        status_code=413,
                        media_type="application/json",
                    )
            except ValueError:
                pass  # Non-numeric Content-Length -- let downstream handle
        return await call_next(request)
