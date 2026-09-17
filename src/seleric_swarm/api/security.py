"""API security — optional API key + per-client rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from hmac import compare_digest

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from seleric_swarm.conversations.contracts import Principal, PrincipalAuthMethod


def _client_key(request: Request, *, trust_x_forwarded_for: bool = False) -> str:
    api_key = request.headers.get("x-api-key") or ""
    if api_key:
        return f"key:{api_key[:16]}"
    forwarded = request.headers.get("x-forwarded-for") if trust_x_forwarded_for else None
    if forwarded and forwarded.split(",")[0].strip():
        return f"ip:{forwarded.split(',')[0].strip()}"
    if request.client:
        return f"ip:{request.client.host}"
    return "ip:unknown"


def _is_exempt(path: str) -> bool:
    return path in {"/", "/health", "/readyz", "/docs", "/openapi.json", "/redoc"}


def _identity_header(request: Request, *names: str, default: str) -> str:
    for name in names:
        value = (request.headers.get(name) or "").strip()
        if value:
            return value
    return default.strip()


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
        default_workspace_id: str = "default",
        default_user_id: str = "default",
        trust_x_forwarded_for: bool = False,
        trust_identity_headers: bool = False,
    ) -> None:
        super().__init__(app)
        self.api_key = (api_key or "").strip()
        self.rate_limit_enabled = rate_limit_enabled and rate_limit_per_minute > 0
        self.limiter = SlidingWindowRateLimiter(limit=rate_limit_per_minute, window_s=60.0)
        self.default_workspace_id = default_workspace_id
        self.default_user_id = default_user_id
        self.trust_x_forwarded_for = trust_x_forwarded_for
        self.trust_identity_headers = trust_identity_headers

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        # Prefer live settings so .env loaded after import still applies.
        api_key = self.api_key
        workspace_id = self.default_workspace_id
        user_id = self.default_user_id
        trust_x_forwarded_for = self.trust_x_forwarded_for
        trust_identity_headers = self.trust_identity_headers
        try:
            from seleric_swarm.config.settings import get_settings

            settings = get_settings()
            api_key = (settings.api_key or "").strip() or api_key
        except Exception:  # noqa: S110 - fall back to the boot-time key on any settings error
            pass

        provided = (request.headers.get("x-api-key") or "").strip()
        auth = (request.headers.get("authorization") or "").strip()
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        authenticated = not api_key or bool(
            (provided and compare_digest(provided, api_key))
            or (bearer and compare_digest(bearer, api_key))
        )

        # Identity headers are trusted-proxy inputs, not authentication. Require both
        # the shared key and an explicit trusted-proxy deployment policy.
        if authenticated and trust_identity_headers:
            workspace_id = _identity_header(
                request,
                "x-workspace-id",
                "x-seleric-workspace-id",
                default=workspace_id,
            )
            user_id = _identity_header(
                request,
                "x-user-id",
                "x-seleric-user-id",
                default=user_id,
            )
        workspace_id = workspace_id.strip() or "default"
        user_id = user_id.strip() or "default"
        principal_id = (
            _identity_header(request, "x-principal-id", default=user_id)
            if authenticated and trust_identity_headers
            else user_id
        )
        roles = {
            role.strip().lower()
            for role in (
                _identity_header(request, "x-roles", "x-seleric-roles", default="")
                if authenticated and trust_identity_headers
                else ""
            ).split(",")
            if role.strip()
        }
        request.state.principal = Principal(
            principal_id=principal_id,
            workspace_id=workspace_id,
            user_id=user_id,
            authenticated=authenticated,
            auth_method=(
                PrincipalAuthMethod.SHARED_API_KEY
                if api_key and authenticated
                else PrincipalAuthMethod.SERVICE
                if authenticated
                else PrincipalAuthMethod.ANONYMOUS
            ),
            roles=roles,
        )

        if _is_exempt(path) or request.method == "OPTIONS":
            return await call_next(request)

        # Optional shared API key (enabled when SELERIC_API_KEY / settings.api_key set).
        if api_key and not authenticated:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        if self.rate_limit_enabled:
            key = _client_key(request, trust_x_forwarded_for=trust_x_forwarded_for)
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
