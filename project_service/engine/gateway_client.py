import asyncio
import json
import logging
import random
import time
from typing import Any, Optional

import httpx

from project_service import config, metrics

logger = logging.getLogger(__name__)


def _upstream_name(base_url: str) -> str:
    # collapse to a short stable label per upstream for metrics keys.
    for label, url in (
        ("gateway", config.GATEWAY_URL),
        ("rag", config.RAG_BASE_URL),
        ("agent", config.AGENT_STUDIO_URL),
        ("artifacts", config.ARTIFACTS_URL),
        ("identity", config.IDENTITY_URL),
    ):
        if base_url == url:
            return label
    return base_url.split("//")[-1].split("/")[0]


class GatewayError(Exception):
    pass


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# methods safe to retry on any error: the operation is read-only or idempotent.
# POST/DELETE/PATCH are NOT retried on HTTPStatusError (already reached the
# server -> side effect may have applied) — only on transport errors where the
# request never left the client. Prevents duplicate KB/doc creation.
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 0.4


def _retry_backoff(attempt: int) -> float:
    # full jitter: uniform in [0, base * 2**attempt]. avoids thundering herd
    # when many concurrent requests retry a 503/429 in lockstep.
    return random.uniform(0.0, _RETRY_BACKOFF_BASE * (2 ** attempt))


class GatewayClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._gateway_url = config.GATEWAY_URL
        self._rag_url = config.RAG_BASE_URL
        self._agent_url = config.AGENT_STUDIO_URL
        self._artifacts_url = config.ARTIFACTS_URL
        self._identity_url = config.IDENTITY_URL
        self._identity_service_token = config.IDENTITY_SERVICE_TOKEN
        self._api_key = config.GATEWAY_API_KEY
        # per-upstream isolated pools so a long-lived gateway stream connection
        # cannot starve RAG/agent/artifacts requests (H3 fix). each pool capped
        # independently with explicit limits + per-upstream timeout.
        self._pool_limits = httpx.Limits(
            max_connections=config.GATEWAY_POOL_MAX_CONN,
            max_keepalive_connections=config.GATEWAY_POOL_MAX_KEEPALIVE,
        )
        self._http_gateway = httpx.AsyncClient(timeout=timeout, limits=self._pool_limits)
        self._http_rag = httpx.AsyncClient(timeout=timeout, limits=self._pool_limits)
        self._http_agent = httpx.AsyncClient(timeout=timeout, limits=self._pool_limits)
        self._http_artifacts = httpx.AsyncClient(timeout=10.0, limits=self._pool_limits)
        # verified-token cache: token -> (claims, expiry_ts). cuts the sync
        # blocking verify call for repeated requests (VerifyJwt is sync by
        # fusion-core contract, so each uncached verify blocks the loop thread).
        self._verify_cache: dict[str, tuple[dict, float]] = {}
        # M14: negative cache — a denied/failed verify is remembered for the TTL
        # so a flood of requests with the same bad token doesn't each block the
        # event-loop thread on a sync HTTP roundtrip. value is expiry_ts only.
        self._verify_neg_cache: dict[str, float] = {}
        self._verify_cache_ttl = config.IDENTITY_VERIFY_CACHE_TTL
        self._verify_client = httpx.Client(timeout=config.IDENTITY_VERIFY_TIMEOUT)
        self._verify_async_client = httpx.AsyncClient(timeout=config.IDENTITY_VERIFY_TIMEOUT)
        logger.info("GatewayClient ready gateway=%s rag=%s agent=%s artifacts=%s auth=%s",
                     self._gateway_url, self._rag_url, self._agent_url, self._artifacts_url,
                     "on" if self._api_key else "off")

    async def close(self) -> None:
        for name, client in (
            ("gateway", self._http_gateway),
            ("rag", self._http_rag),
            ("agent", self._http_agent),
            ("artifacts", self._http_artifacts),
        ):
            try:
                await client.aclose()
            except Exception as e:
                logger.warning("gateway client %s close failed: %s", name, e)
        try:
            self._verify_client.close()
        except Exception as e:
            logger.warning("identity verify client close failed: %s", e)
        try:
            await self._verify_async_client.aclose()
        except Exception as e:
            logger.warning("identity verify async client close failed: %s", e)
        logger.info("GatewayClient closed")

    def _auth_headers(self) -> dict:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _client_for(self, base_url: str) -> httpx.AsyncClient:
        if base_url == self._gateway_url:
            return self._http_gateway
        if base_url == self._rag_url:
            return self._http_rag
        if base_url == self._agent_url:
            return self._http_agent
        if base_url == self._artifacts_url:
            return self._http_artifacts
        return self._http_gateway

    async def _request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        retries: int = _MAX_RETRIES,
    ) -> dict:
        url = f"{base_url}{path}"
        client = self._client_for(base_url)
        up = _upstream_name(base_url)
        last_exc: Optional[Exception] = None
        last_status: Optional[int] = None
        retries_done = 0
        t0 = time.monotonic()
        try:
            for attempt in range(retries + 1):
                try:
                    resp = await client.request(
                        method, url, json=json_data, params=params,
                        headers=self._auth_headers(),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    metrics.record_gateway(up, ok=True, retries=retries_done, latency=time.monotonic() - t0)
                    return data
                except httpx.HTTPStatusError as e:
                    last_status = e.response.status_code
                    last_exc = e
                    if e.response.status_code not in _RETRYABLE_STATUS:
                        logger.error("gateway %s %s -> %d (non-retryable): %s", method, url, e.response.status_code, e)
                        raise GatewayError(f"{method} {url} -> {e.response.status_code}: {e}") from e
                    if method.upper() not in _IDEMPOTENT_METHODS:
                        logger.error("gateway %s %s -> %d (non-idempotent, not retrying): %s", method, url, e.response.status_code, e)
                        raise GatewayError(f"{method} {url} -> {e.response.status_code}: {e}") from e
                    retries_done += 1
                    logger.warning("gateway %s %s -> %d (retry %d/%d)", method, url, e.response.status_code, attempt + 1, retries)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    retries_done += 1
                    logger.warning("gateway %s %s transient error (retry %d/%d): %s", method, url, attempt + 1, retries, e)
                except httpx.RequestError as e:
                    last_exc = e
                    logger.error("gateway %s %s request error (non-retryable): %s", method, url, e)
                    raise GatewayError(f"{method} {url} request error: {e}") from e
                if attempt < retries:
                    await asyncio.sleep(_retry_backoff(attempt))
            logger.error("gateway %s %s exhausted %d retries last_status=%s last_err=%s", method, url, retries, last_status, last_exc)
            detail = f"status={last_status}" if last_status is not None else f"err={last_exc}"
            raise GatewayError(f"{method} {url} failed after {retries} retries: {detail}")
        except GatewayError:
            metrics.record_gateway(up, ok=False, retries=retries_done, latency=time.monotonic() - t0)
            raise

    async def _health_check(self, url: str) -> bool:
        client = self._client_for(url.rsplit("/", 1)[0]) if "/" in url else self._http_gateway
        try:
            resp = await client.get(url, timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    # ── RAG (fusion-kb) ──

    async def rag_create_kb(self, name: str, description: str = "", kb_id: str = "", embedding_model: str = "") -> dict:
        payload: dict[str, Any] = {"name": name, "description": description}
        if kb_id:
            payload["kb_id"] = kb_id
        if embedding_model:
            payload["embedding_model"] = embedding_model
        return await self._request(self._rag_url, "POST", "/kb/bases", json_data=payload)

    async def rag_upload_doc(self, kb_id: str, file_path: str, contextualize: bool = True) -> dict:
        payload = {"file_path": file_path, "contextualize": contextualize}
        return await self._request(self._rag_url, "POST", f"/kb/bases/{kb_id}/documents", json_data=payload)

    async def rag_batch_upload(self, kb_id: str, file_paths: list[str], contextualize: bool = True) -> dict:
        payload = {"file_paths": file_paths, "contextualize": contextualize}
        return await self._request(self._rag_url, "POST", f"/kb/bases/{kb_id}/documents/batch", json_data=payload)

    async def rag_search(self, kb_id: str, query: str, *, top_k: int = 5, folder_prefix: str | None = None) -> dict:
        payload: dict = {"query": query, "top_k": top_k}
        if folder_prefix:
            payload["folder_prefix"] = folder_prefix
        return await self._request(self._rag_url, "POST", f"/kb/bases/{kb_id}/search", json_data=payload)

    async def rag_ask(self, kb_id: str, query: str, *, top_k: int = 5) -> dict:
        payload = {"query": query, "top_k": top_k}
        return await self._request(self._rag_url, "POST", f"/kb/bases/{kb_id}/ask", json_data=payload)

    async def rag_list_docs(self, kb_id: str) -> dict:
        return await self._request(self._rag_url, "GET", f"/kb/bases/{kb_id}/documents")

    async def rag_delete_doc(self, kb_id: str, doc_id: str) -> dict:
        return await self._request(self._rag_url, "DELETE", f"/kb/bases/{kb_id}/documents/{doc_id}")

    async def rag_get_kb(self, kb_id: str) -> dict:
        return await self._request(self._rag_url, "GET", f"/kb/bases/{kb_id}")

    async def rag_kb_status(self, kb_id: str) -> int:
        url = f"{self._rag_url}/kb/bases/{kb_id}"
        try:
            resp = await self._http_rag.get(url, timeout=10.0, headers=self._auth_headers())
            return resp.status_code
        except Exception as e:
            logger.warning("rag kb status probe error kb_id=%s err=%s", kb_id, e)
            return -1

    async def rag_delete_kb(self, kb_id: str) -> dict:
        return await self._request(self._rag_url, "DELETE", f"/kb/bases/{kb_id}")

    async def rag_get_stats(self, kb_id: str) -> dict:
        return await self._request(self._rag_url, "GET", f"/kb/bases/{kb_id}/stats")

    async def rag_is_healthy(self) -> bool:
        return await self._health_check(f"{self._rag_url}/health")

    # ── Agent Studio ──

    async def agent_list(self) -> dict:
        return await self._request(self._agent_url, "GET", "/api/v1/agents")

    async def agent_get(self, agent_id: str) -> dict:
        return await self._request(self._agent_url, "GET", f"/api/v1/agents/{agent_id}")

    async def agent_studio_is_healthy(self) -> bool:
        return await self._health_check(f"{self._agent_url}/health")

    # ── Gateway (LLM inference) ──

    async def chat_completions_stream(self, messages: list[dict], *, model: str = "", temperature: float = 0.7, max_tokens: int = 4096):
        url = f"{self._gateway_url}/v1/chat/completions"
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if model:
            payload["model"] = model
        try:
            async with self._http_gateway.stream("POST", url, json=payload, timeout=120.0, headers=self._auth_headers()) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        yield chunk
                    except json.JSONDecodeError:
                        logger.warning("stream chunk parse error: %s", data[:120])
        except httpx.HTTPStatusError as e:
            logger.error("chat completions stream %d: %s", e.response.status_code, e)
            raise GatewayError(f"chat stream -> {e.response.status_code}: {e}") from e
        except httpx.RequestError as e:
            logger.error("chat completions stream error: %s", e)
            raise GatewayError(f"chat stream request error: {e}") from e

    async def gateway_is_healthy(self) -> bool:
        return await self._health_check(f"{self._gateway_url}/health")

    async def artifacts_is_healthy(self) -> bool:
        return await self._health_check(f"{self._artifacts_url}/health")

    async def artifacts_call(self, method: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        last_exc: Optional[Exception] = None
        last_status: Optional[int] = None
        retries_done = 0
        t0 = time.monotonic()
        try:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = await self._http_artifacts.post(self._artifacts_url, json=payload, headers=self._auth_headers(), timeout=10.0)
                    resp.raise_for_status()
                    data = resp.json()
                    if "error" in data:
                        raise GatewayError(f"artifacts {method} rpc_error: {data['error']}")
                    metrics.record_gateway("artifacts", ok=True, retries=retries_done, latency=time.monotonic() - t0)
                    return data.get("result", {})
                except httpx.HTTPStatusError as e:
                    last_status = e.response.status_code
                    last_exc = e
                    if e.response.status_code not in _RETRYABLE_STATUS:
                        logger.error("artifacts %s -> %d (non-retryable): %s", method, e.response.status_code, e)
                        raise GatewayError(f"artifacts {method} -> {e.response.status_code}: {e}") from e
                    logger.error("artifacts %s -> %d (non-idempotent, not retrying): %s", method, e.response.status_code, e)
                    raise GatewayError(f"artifacts {method} -> {e.response.status_code}: {e}") from e
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    retries_done += 1
                    logger.warning("artifacts %s transient error (retry %d/%d): %s", method, attempt + 1, _MAX_RETRIES, e)
                except httpx.RequestError as e:
                    last_exc = e
                    logger.error("artifacts %s request error (non-retryable): %s", method, e)
                    raise GatewayError(f"artifacts {method} request error: {e}") from e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_retry_backoff(attempt))
            detail = f"status={last_status}" if last_status is not None else f"err={last_exc}"
            raise GatewayError(f"artifacts {method} failed after {_MAX_RETRIES} retries: {detail}")
        except GatewayError:
            metrics.record_gateway("artifacts", ok=False, retries=retries_done, latency=time.monotonic() - t0)
            raise

    # ── fusion-identity (tenant registry + JWT issuer) ──
    # async verify is the primary path: fusion-core's VerifyJwt contract accepts
    # an Awaitable, and TenantMiddleware auto-awaits it (fusion-core#24 closed).
    # This keeps the event loop unblocked on every cache miss — M16 fixed.
    # identity_verify_sync stays for the UDS daemon / MCP paths where the
    # callback may be invoked outside an async verify callable, and for tests.

    async def identity_verify(self, token: str) -> dict:
        if not self._identity_service_token:
            raise GatewayError("identity service token not configured")
        # short-TTL positive cache: a verified token is reused for a few seconds
        # so repeated requests don't each round-trip to fusion-identity.
        # M14: negative cache — a denied/failed verify is remembered for the TTL
        # so a flood of bad-token requests doesn't each round-trip.
        ttl = self._verify_cache_ttl
        now = time.monotonic()
        if ttl > 0:
            cached = self._verify_cache.get(token)
            if cached and cached[1] > now:
                metrics.record_identity_verify(ok=True, cached=True)
                return cached[0]
            neg = self._verify_neg_cache.get(token)
            if neg and neg > now:
                metrics.record_identity_verify(ok=False, cached=True)
                raise GatewayError("identity verify cached denial")
        url = f"{self._identity_url}/api/v1/auth/verify"
        headers = {"Authorization": f"Bearer {self._identity_service_token}"}
        try:
            resp = await self._verify_async_client.post(url, json={"token": token}, headers=headers)
            resp.raise_for_status()
            claims = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("identity verify %d: %s", e.response.status_code, e)
            if ttl > 0:
                self._verify_neg_cache[token] = now + ttl
                if len(self._verify_neg_cache) > 1024:
                    self._verify_neg_cache = {k: v for k, v in self._verify_neg_cache.items() if v > now}
            metrics.record_identity_verify(ok=False)
            raise GatewayError(f"identity verify -> {e.response.status_code}") from e
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning("identity verify error: %s", e)
            if ttl > 0:
                self._verify_neg_cache[token] = now + ttl
                if len(self._verify_neg_cache) > 1024:
                    self._verify_neg_cache = {k: v for k, v in self._verify_neg_cache.items() if v > now}
            metrics.record_identity_verify(ok=False)
            raise GatewayError(f"identity verify error: {e}") from e
        if ttl > 0:
            self._verify_cache[token] = (claims, now + ttl)
            self._verify_neg_cache.pop(token, None)
            if len(self._verify_cache) > 1024:
                self._verify_cache = {k: v for k, v in self._verify_cache.items() if v[1] > now}
        metrics.record_identity_verify(ok=True)
        return claims

    def identity_verify_sync(self, token: str) -> dict:
        if not self._identity_service_token:
            raise GatewayError("identity service token not configured")
        # sync fallback for UDS/MCP paths invoked outside an async verify
        # callable, and for tests. positive + negative caches mirror the async
        # path; the sync httpx.Client still blocks the calling thread, so this
        # path must NOT be wired into the REST middleware (use identity_verify).
        ttl = self._verify_cache_ttl
        now = time.monotonic()
        if ttl > 0:
            cached = self._verify_cache.get(token)
            if cached and cached[1] > now:
                metrics.record_identity_verify(ok=True, cached=True)
                return cached[0]
            neg = self._verify_neg_cache.get(token)
            if neg and neg > now:
                metrics.record_identity_verify(ok=False, cached=True)
                raise GatewayError("identity verify cached denial")
        url = f"{self._identity_url}/api/v1/auth/verify"
        headers = {"Authorization": f"Bearer {self._identity_service_token}"}
        try:
            resp = self._verify_client.post(url, json={"token": token}, headers=headers)
            resp.raise_for_status()
            claims = resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("identity verify %d: %s", e.response.status_code, e)
            if ttl > 0:
                self._verify_neg_cache[token] = now + ttl
                if len(self._verify_neg_cache) > 1024:
                    self._verify_neg_cache = {k: v for k, v in self._verify_neg_cache.items() if v > now}
            metrics.record_identity_verify(ok=False)
            raise GatewayError(f"identity verify -> {e.response.status_code}") from e
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning("identity verify error: %s", e)
            if ttl > 0:
                self._verify_neg_cache[token] = now + ttl
                if len(self._verify_neg_cache) > 1024:
                    self._verify_neg_cache = {k: v for k, v in self._verify_neg_cache.items() if v > now}
            metrics.record_identity_verify(ok=False)
            raise GatewayError(f"identity verify error: {e}") from e
        if ttl > 0:
            self._verify_cache[token] = (claims, now + ttl)
            self._verify_neg_cache.pop(token, None)
            if len(self._verify_cache) > 1024:
                self._verify_cache = {k: v for k, v in self._verify_cache.items() if v[1] > now}
        metrics.record_identity_verify(ok=True)
        return claims

    async def identity_emit_usage(self, tenant_id: str, metric: str, value: int, *, source: str = "fusion-projects", model: Optional[str] = None, user_id: Optional[str] = None) -> None:
        if not self._identity_service_token:
            return
        url = f"{self._identity_url}/api/v1/tenants/{tenant_id}/usage"
        headers = {"Authorization": f"Bearer {self._identity_service_token}"}
        payload: dict[str, Any] = {"metric": metric, "value": value, "source": source}
        if model:
            payload["model"] = model
        if user_id:
            payload["user_id"] = user_id
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
        except Exception as e:
            logger.warning("identity usage emit failed tenant=%s metric=%s err=%s", tenant_id, metric, e)
