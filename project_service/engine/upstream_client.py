import asyncio
import logging
import time
from enum import Enum
from typing import Any, Optional

import httpx

from project_service import config

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float = 0
        self.half_open_calls = 0

    def can_call(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                logger.info("circuit %s: OPEN -> HALF_OPEN", self.name)
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls < self.half_open_max:
                return True
            return False
        return False

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info("circuit %s: HALF_OPEN -> CLOSED (recovered)", self.name)
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.half_open_calls = 0
            logger.warning("circuit %s: HALF_OPEN -> OPEN (probe failed)", self.name)
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(
                    "circuit %s: CLOSED -> OPEN (failures=%d)", self.name, self.failure_count
                )

    def on_call_start(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1

    @property
    def is_available(self) -> bool:
        return self.state != CircuitState.OPEN or self.can_call()


class UpstreamClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._mlx_api_key = config.MLX_API_KEY
        headers = {}
        if self._mlx_api_key:
            headers["Authorization"] = f"Bearer {self._mlx_api_key}"
        self._http = httpx.AsyncClient(timeout=timeout, headers=headers)
        self._circuits = {
            "mlx": CircuitBreaker("mlx", failure_threshold=5, recovery_timeout=30.0),
            "rag": CircuitBreaker("rag", failure_threshold=5, recovery_timeout=30.0),
            "agent_studio": CircuitBreaker("agent_studio", failure_threshold=5, recovery_timeout=30.0),
            "gateway": CircuitBreaker("gateway", failure_threshold=3, recovery_timeout=20.0),
        }
        logger.info("UpstreamClient ready timeout=%.1f", timeout)

    async def close(self) -> None:
        await self._http.aclose()
        logger.info("UpstreamClient closed")

    def _circuit(self, service: str) -> CircuitBreaker:
        cb = self._circuits.get(service)
        if cb is None:
            raise ValueError(f"unknown service: {service}")
        return cb

    async def _request(
        self,
        service: str,
        method: str,
        url: str,
        *,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        cb = self._circuit(service)
        if not cb.can_call():
            logger.warning("circuit %s OPEN, rejecting call to %s", service, url)
            return {"error": f"circuit_open", "service": service, "detail": "service unavailable"}
        cb.on_call_start()
        try:
            resp = await self._http.request(
                method,
                url,
                json=json_data,
                params=params,
                headers=headers,
            )
            resp.raise_for_status()
            cb.record_success()
            return resp.json()
        except httpx.HTTPStatusError as e:
            cb.record_failure()
            logger.error("upstream %s %s -> %d: %s", method, url, e.response.status_code, e)
            return {
                "error": "http_error",
                "service": service,
                "status": e.response.status_code,
                "detail": str(e),
            }
        except httpx.RequestError as e:
            cb.record_failure()
            logger.error("upstream %s %s request error: %s", method, url, e)
            return {"error": "request_error", "service": service, "detail": str(e)}

    # ── MLX (LLM Inference) ──

    async def mlx_chat(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        stream: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> dict:
        payload: dict[str, Any] = {
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }
        if model:
            payload["model"] = model
        return await self._request("mlx", "POST", f"{config.MLX_BASE_URL}/chat/completions", json_data=payload)

    async def mlx_models(self) -> dict:
        return await self._request("mlx", "GET", f"{config.MLX_BASE_URL}/models")

    async def mlx_is_healthy(self) -> bool:
        cb = self._circuit("mlx")
        if not cb.can_call():
            return False
        try:
            resp = await self._http.get(f"{config.MLX_BASE_URL}/models", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    # ── RAG (fusion-rag / fusion-kb) ──

    async def rag_create_kb(self, name: str, description: str = "", kb_id: str = "", embedding_model: str = "") -> dict:
        payload: dict[str, Any] = {"name": name, "description": description}
        if kb_id:
            payload["kb_id"] = kb_id
        if embedding_model:
            payload["embedding_model"] = embedding_model
        return await self._request("rag", "POST", f"{config.RAG_BASE_URL}/kb/bases", json_data=payload)

    async def rag_upload_doc(self, kb_id: str, file_path: str, contextualize: bool = True) -> dict:
        payload = {"file_path": file_path, "contextualize": contextualize}
        return await self._request("rag", "POST", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}/documents", json_data=payload)

    async def rag_batch_upload(self, kb_id: str, file_paths: list[str], contextualize: bool = True) -> dict:
        payload = {"file_paths": file_paths, "contextualize": contextualize}
        return await self._request("rag", "POST", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}/documents/batch", json_data=payload)

    async def rag_search(self, kb_id: str, query: str, *, top_k: int = 5) -> dict:
        payload = {"query": query, "top_k": top_k}
        return await self._request("rag", "POST", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}/search", json_data=payload)

    async def rag_ask(self, kb_id: str, query: str, *, top_k: int = 5) -> dict:
        payload = {"query": query, "top_k": top_k}
        return await self._request("rag", "POST", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}/ask", json_data=payload)

    async def rag_list_docs(self, kb_id: str) -> dict:
        return await self._request("rag", "GET", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}/documents")

    async def rag_delete_doc(self, kb_id: str, doc_id: str) -> dict:
        return await self._request("rag", "DELETE", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}/documents/{doc_id}")

    async def rag_get_kb(self, kb_id: str) -> dict:
        return await self._request("rag", "GET", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}")

    async def rag_delete_kb(self, kb_id: str) -> dict:
        return await self._request("rag", "DELETE", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}")

    async def rag_get_stats(self, kb_id: str) -> dict:
        return await self._request("rag", "GET", f"{config.RAG_BASE_URL}/kb/bases/{kb_id}/stats")

    async def rag_is_healthy(self) -> bool:
        cb = self._circuit("rag")
        if not cb.can_call():
            return False
        try:
            resp = await self._http.get(f"{config.RAG_BASE_URL}/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    # ── Agent Studio ──

    async def agent_list(self) -> dict:
        return await self._request("agent_studio", "GET", f"{config.AGENT_STUDIO_URL}/api/v1/agents")

    async def agent_get(self, agent_id: str) -> dict:
        return await self._request("agent_studio", "GET", f"{config.AGENT_STUDIO_URL}/api/v1/agents/{agent_id}")

    async def agent_execute(self, agent_id: str, payload: dict) -> dict:
        return await self._request(
            "agent_studio", "POST", f"{config.AGENT_STUDIO_URL}/api/v1/agents/{agent_id}/execute", json_data=payload
        )

    async def agent_studio_is_healthy(self) -> bool:
        cb = self._circuit("agent_studio")
        if not cb.can_call():
            return False
        try:
            resp = await self._http.get(f"{config.AGENT_STUDIO_URL}/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    # ── Gateway ──

    async def gateway_chat(self, payload: dict) -> dict:
        return await self._request("gateway", "POST", f"{config.GATEWAY_URL}/api/v1/chat", json_data=payload)

    async def gateway_is_healthy(self) -> bool:
        cb = self._circuit("gateway")
        if not cb.can_call():
            return False
        try:
            resp = await self._http.get(f"{config.GATEWAY_URL}/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    # ── Circuit Status ──

    def get_circuit_status(self) -> dict[str, str]:
        return {name: cb.state.value for name, cb in self._circuits.items()}

    async def health_check_all(self) -> dict[str, bool]:
        results = {}
        checks = {
            "mlx": self.mlx_is_healthy(),
            "rag": self.rag_is_healthy(),
            "agent_studio": self.agent_studio_is_healthy(),
            "gateway": self.gateway_is_healthy(),
        }
        for name, coro in checks.items():
            try:
                results[name] = await coro
            except Exception:
                results[name] = False
        logger.info("health_check_all: %s", results)
        return results
