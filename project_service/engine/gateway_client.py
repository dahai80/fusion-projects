import asyncio
import json
import logging
from typing import Any, Optional

import httpx

from project_service import config

logger = logging.getLogger(__name__)


class GatewayError(Exception):
    pass


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 0.4


class GatewayClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._gateway_url = config.GATEWAY_URL
        self._rag_url = config.RAG_BASE_URL
        self._agent_url = config.AGENT_STUDIO_URL
        self._artifacts_url = config.ARTIFACTS_URL
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
        last_exc: Optional[Exception] = None
        last_status: Optional[int] = None
        for attempt in range(retries + 1):
            try:
                resp = await client.request(
                    method, url, json=json_data, params=params,
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                last_status = e.response.status_code
                last_exc = e
                if e.response.status_code not in _RETRYABLE_STATUS:
                    logger.error("gateway %s %s -> %d (non-retryable): %s", method, url, e.response.status_code, e)
                    raise GatewayError(f"{method} {url} -> {e.response.status_code}: {e}") from e
                logger.warning("gateway %s %s -> %d (retry %d/%d)", method, url, e.response.status_code, attempt + 1, retries)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                logger.warning("gateway %s %s transient error (retry %d/%d): %s", method, url, attempt + 1, retries, e)
            except httpx.RequestError as e:
                last_exc = e
                logger.error("gateway %s %s request error (non-retryable): %s", method, url, e)
                raise GatewayError(f"{method} {url} request error: {e}") from e
            if attempt < retries:
                backoff = _RETRY_BACKOFF_BASE * (2 ** attempt)
                await asyncio.sleep(backoff)
        logger.error("gateway %s %s exhausted %d retries last_status=%s last_err=%s", method, url, retries, last_status, last_exc)
        detail = f"status={last_status}" if last_status is not None else f"err={last_exc}"
        raise GatewayError(f"{method} {url} failed after {retries} retries: {detail}")

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
            yield {"error": "http_error", "status": e.response.status_code}
        except httpx.RequestError as e:
            logger.error("chat completions stream error: %s", e)
            yield {"error": "request_error", "detail": str(e)}

    async def gateway_is_healthy(self) -> bool:
        return await self._health_check(f"{self._gateway_url}/health")

    async def artifacts_is_healthy(self) -> bool:
        return await self._health_check(f"{self._artifacts_url}/health")

    async def artifacts_call(self, method: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        last_exc: Optional[Exception] = None
        last_status: Optional[int] = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._http_artifacts.post(self._artifacts_url, json=payload, headers=self._auth_headers(), timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    raise GatewayError(f"artifacts {method} rpc_error: {data['error']}")
                return data.get("result", {})
            except httpx.HTTPStatusError as e:
                last_status = e.response.status_code
                last_exc = e
                if e.response.status_code not in _RETRYABLE_STATUS:
                    logger.error("artifacts %s -> %d (non-retryable): %s", method, e.response.status_code, e)
                    raise GatewayError(f"artifacts {method} -> {e.response.status_code}: {e}") from e
                logger.warning("artifacts %s -> %d (retry %d/%d)", method, e.response.status_code, attempt + 1, _MAX_RETRIES)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                logger.warning("artifacts %s transient error (retry %d/%d): %s", method, attempt + 1, _MAX_RETRIES, e)
            except httpx.RequestError as e:
                last_exc = e
                logger.error("artifacts %s request error (non-retryable): %s", method, e)
                raise GatewayError(f"artifacts {method} request error: {e}") from e
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF_BASE * (2 ** attempt))
        detail = f"status={last_status}" if last_status is not None else f"err={last_exc}"
        raise GatewayError(f"artifacts {method} failed after {_MAX_RETRIES} retries: {detail}")
