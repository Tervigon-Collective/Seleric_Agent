"""API security — optional API key + per-client rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _client_key(request: Request) -> str:
    api_key = request.headers.get("x-api-key") or ""
    if api_key:
        return f"key:{api_key[:16]}"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    if request.client:
        return f"ip:{request.client.host}"
    return "ip:unknown"


def _is_exempt(path: str) -> bool:
    return path in {"/", "/health", "/readyz", "/docs", "/openapi.json", "/redoc"}


class SlidingWindowRateLimiter:
    """In-process sliding window limiter (per API worker)."""

    def __init__(self, *, limit: int, window_s: float = 60.0) -> None:
        self.limit = max(1, int(limit))
        self.window_s = float(window_s)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, now: float | None = None) -> tuple[bool, int, int]:
        """Return (allowed, remaining, retry_after_s)."""
        ts = now if now is not None else time.monotonic()
        q = self._hits[key]
        cutoff = ts - self.window_s
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self.limit:
            retry = int(max(1, self.window_s - (ts - q[0])))
            return False, 0, retry
        q.append(ts)
        return True, self.limit - len(q), 0


class ApiSecurityMiddleware(BaseHTTPMiddleware):
    """Enforce optional API key and rate limits on mutating / mission routes."""

    def __init__(
        self,
        app: Callable,
        *,
        api_key: str = "",
        rate_limit_per_minute: int = 60,
        rate_limit_enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self.api_key = (api_key or "").strip()
        self.rate_limit_enabled = rate_limit_enabled and rate_limit_per_minute > 0
        self.limiter = SlidingWindowRateLimiter(limit=rate_limit_per_minute, window_s=60.0)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)

        # Optional shared API key (enabled when SELERIC_API_KEY / settings.api_key set).
        if self.api_key:
            provided = (request.headers.get("x-api-key") or "").strip()
            auth = (request.headers.get("authorization") or "").strip()
            bearer = ""
            if auth.lower().startswith("bearer "):
                bearer = auth[7:].strip()
            if provided != self.api_key and bearer != self.api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid API key"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

        if self.rate_limit_enabled:
            key = _client_key(request)
            ok, remaining, retry_after = self.limiter.allow(key)
            if not ok:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded", "retry_after_s": retry_after},
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(self.limiter.limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(self.limiter.limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        return await call_next(request)
