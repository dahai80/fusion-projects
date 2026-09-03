import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from project_service import config
from project_service import metrics

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = ("/health", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    xkey = request.headers.get("x-api-key", "")
    return xkey.strip()


class AuthMiddleware(BaseHTTPMiddleware):
    _warned_no_auth = False

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        response = await self._check(request, call_next)
        metrics.record_request(path, response.status_code)
        return response

    async def _check(self, request: Request, call_next):
        if not config.REST_API_KEY:
            if not config.REST_ALLOW_NO_AUTH:
                logger.critical(
                    "REST auth disabled and FUSION_REST_ALLOW_NO_AUTH not set — "
                    "service running UNAUTHENTICATED (loopback only, non-loopback "
                    "bind is refused at startup). Set FUSION_REST_API_KEY or "
                    "FUSION_REST_ALLOW_NO_AUTH=1 to acknowledge."
                )
            elif not AuthMiddleware._warned_no_auth:
                logger.warning(
                    "REST auth disabled (FUSION_REST_ALLOW_NO_AUTH=1, acknowledged)"
                )
                AuthMiddleware._warned_no_auth = True
            return await call_next(request)
        path = request.url.path
        if path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        token = _extract_bearer(request)
        if not token:
            client = request.client.host if request.client else "?"
            logger.warning("rest auth missing token path=%s client=%s", path, client)
            metrics.record_auth_reject()
            return JSONResponse(status_code=401, content={"detail": "missing authorization"})
        if token != config.REST_API_KEY:
            client = request.client.host if request.client else "?"
            logger.warning("rest auth invalid token path=%s client=%s", path, client)
            metrics.record_auth_reject()
            return JSONResponse(status_code=403, content={"detail": "invalid api key"})
        return await call_next(request)


class BodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > config.REST_MAX_BODY_BYTES:
            logger.warning("rest body too large path=%s size=%s", request.url.path, cl)
            metrics.record_body_oversize()
            return JSONResponse(status_code=413, content={"detail": "request body too large"})
        return await call_next(request)


class _RateLimiter:
    def __init__(self, limit: int, window: float, max_ips: int) -> None:
        self.limit = limit
        self.window = window
        self.max_ips = max_ips
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits.get(key, [])
        cutoff = now - self.window
        fresh = [t for t in bucket if t > cutoff]
        if len(fresh) >= self.limit:
            self._hits[key] = fresh
            return False
        if key not in self._hits and len(self._hits) >= self.max_ips:
            self._evict()
        fresh.append(now)
        self._hits[key] = fresh
        return True

    def _evict(self) -> None:
        now = time.monotonic()
        cutoff = now - self.window
        stale = [k for k, v in self._hits.items() if not any(t > cutoff for t in v)]
        for k in stale:
            del self._hits[k]
        if len(self._hits) >= self.max_ips:
            oldest = sorted(self._hits, key=lambda k: min(self._hits[k]) if self._hits[k] else now)
            for k in oldest[: max(1, len(self._hits) - self.max_ips + 1)]:
                del self._hits[k]
            logger.warning("rate limiter ip cap reached max_ips=%d evicted to %d", self.max_ips, len(self._hits))


_rate_limiter: Optional[_RateLimiter] = None


def _get_rate_limiter() -> _RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = _RateLimiter(config.REST_RATE_LIMIT, config.REST_RATE_WINDOW, config.RATE_MAX_IPS)
    return _rate_limiter


def reset_rate_limiter() -> None:
    global _rate_limiter
    _rate_limiter = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        rate_key = self._rate_key(request)
        if not _get_rate_limiter().check(rate_key):
            logger.warning("rest rate limit exceeded key=%s path=%s", rate_key, request.url.path)
            metrics.record_rate_limit_reject()
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        return await call_next(request)

    @staticmethod
    def _rate_key(request: Request) -> str:
        try:
            from fusion_core.tenant.context import current as _tenant_current

            ctx = _tenant_current()
        except Exception:
            ctx = None
        if ctx is not None and ctx.tenant_id:
            return f"tenant:{ctx.tenant_id}"
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            client = xff.split(",")[0].strip() or "unknown"
        else:
            client = request.client.host if request.client else "unknown"
        return client
