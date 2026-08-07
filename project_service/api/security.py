import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from project_service import config

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = ("/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    xkey = request.headers.get("x-api-key", "")
    return xkey.strip()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not config.REST_API_KEY:
            logger.debug("REST auth disabled (no REST_API_KEY configured)")
            return await call_next(request)
        path = request.url.path
        if path in _PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        token = _extract_bearer(request)
        if not token:
            client = request.client.host if request.client else "?"
            logger.warning("rest auth missing token path=%s client=%s", path, client)
            return JSONResponse(status_code=401, content={"detail": "missing authorization"})
        if token != config.REST_API_KEY:
            client = request.client.host if request.client else "?"
            logger.warning("rest auth invalid token path=%s client=%s", path, client)
            return JSONResponse(status_code=403, content={"detail": "invalid api key"})
        return await call_next(request)


class BodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > config.REST_MAX_BODY_BYTES:
            logger.warning("rest body too large path=%s size=%s", request.url.path, cl)
            return JSONResponse(status_code=413, content={"detail": "request body too large"})
        return await call_next(request)


class _RateLimiter:
    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - self.window
        self._hits[key] = [t for t in bucket if t > cutoff]
        if len(self._hits[key]) >= self.limit:
            return False
        self._hits[key].append(now)
        return True


_rate_limiter: Optional[_RateLimiter] = None


def _get_rate_limiter() -> _RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = _RateLimiter(config.REST_RATE_LIMIT, config.REST_RATE_WINDOW)
    return _rate_limiter


def reset_rate_limiter() -> None:
    global _rate_limiter
    _rate_limiter = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        if not _get_rate_limiter().check(client):
            logger.warning("rest rate limit exceeded client=%s path=%s", client, request.url.path)
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        return await call_next(request)
