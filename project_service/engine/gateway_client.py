import logging
from typing import Any, Optional

import httpx

from project_service import config

logger = logging.getLogger(__name__)


class GatewayClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._gateway_url = config.GATEWAY_URL
        self._rag_url = config.RAG_BASE_URL
        self._agent_url = config.AGENT_STUDIO_URL
        self._http = httpx.AsyncClient(timeout=timeout)
        logger.info("GatewayClient ready gateway=%s rag=%s agent=%s",
                     self._gateway_url, self._rag_url, self._agent_url)

    async def close(self) -> None:
        await self._http.aclose()
        logger.info("GatewayClient closed")

    async def _request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        url = f"{base_url}{path}"
        try:
            resp = await self._http.request(
                method, url, json=json_data, params=params,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("gateway %s %s -> %d: %s", method, url, e.response.status_code, e)
            return {
                "error": "http_error",
                "status": e.response.status_code,
                "detail": str(e),
            }
        except httpx.RequestError as e:
            logger.error("gateway %s %s request error: %s", method, url, e)
            return {"error": "request_error", "detail": str(e)}

    async def _health_check(self, url: str) -> bool:
        try:
            resp = await self._http.get(url, timeout=5.0)
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

    async def agent_execute(self, agent_id: str, payload: dict) -> dict:
        return await self._request(self._agent_url, "POST", f"/api/v1/agents/{agent_id}/execute", json_data=payload)

    async def agent_studio_is_healthy(self) -> bool:
        return await self._health_check(f"{self._agent_url}/health")

    # ── Gateway ──

    async def gateway_is_healthy(self) -> bool:
        return await self._health_check(f"{self._gateway_url}/health")
