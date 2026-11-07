---
doc_id: fusion-projects/guide
product: fusion-projects
class: app
entry: service
doc_type: guide
title: { zh: "fusion-projects 开发者指南", en: "fusion-projects developer guide" }
audience: engineer
status: draft
version: 0.7.5
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - CLAUDE.md
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

`fusion-project-svc` — a local-first **project asset container** service. A *Project* is an isolated work domain bundling global instructions, a persistent knowledge base (RAG), isolated chat sessions, a bound Fusion Agent, and artifacts. The service owns project metadata, instructions, and the on-disk storage layout, and exposes three surfaces:

- **UDS JSON-RPC daemon** (`project_service.daemon_server`) — primary surface, used by Fusion desktop/agent callers. Newline-delimited JSON-RPC 2.0 over `/tmp/fusion-project-svc.sock`.
- **REST API** (`project_service.api.rest_server`) — FastAPI, prefix `/api/v1`, optional (port 11440). Includes SSE streaming for chat.
- **MCP server** (`project_service.mcp_server`) — stdio JSON-RPC for Claude/Cursor integration.

`project_service/client.py` (`ProjectClient`) is the async UDS client callers use to talk to the daemon; `project_service/metrics.py` holds the in-process counters the REST middleware record into (request/rate-limit/auth/oversize, surfaced via snapshot).

Top-level scripts `bootstrap_douyin_ecommerce.py` / `build_douyin_agentgraph.py` are one-off douyin e-commerce project scaffolding, not part of the service core.

## Build / Run / Test

```bash
cd ~/fusion/fusion-projects
source .venv/bin/activate          # shared monorepo venv also works
pip install -e ".[test]"

./start.sh start|stop|restart|status   # UDS daemon (logs: logs/stdout.log, logs/stderr.log)
python -m project_service.rest_server  # REST on http://127.0.0.1:11440
python -m project_service.mcp_server   # MCP stdio

pytest tests/                          # full suite (asyncio_mode=auto)
pytest tests/test_rpc_handlers.py -v   # single file
pytest tests/test_chat_manager.py::test_name -v   # single test
pytest tests/ -m "not integration"     # skip tests needing live upstreams
```

Tests marked `integration` (`pytestmark = pytest.mark.integration`, see `test_chat_e2e.py`) require live fusion-gateway/rag/mlx upstreams; filter them out for offline runs.

No ruff/lint config is defined in this project's pyproject.toml; do not invent lint commands.

## Architecture

Layering is strict: `config` → `store` (raw sqlite3, no ORM) → `engine` (async managers) → transport (`daemon_server` / `api/routes` / `mcp_server`).

- **`config.py`** — single source of paths, ports, URLs, and defaults, all env-overridable (`FUSION_PROJECT_*`). Data lives under `~/.fusion-projects/` (`data/projects.db`, `storage/<project_id>/...`). Defaults: RAG mode `AUTO`, top_k 5, threshold 0.65, prompt merge `AGENT_FIRST`.
- **`store/project_store.py`** — `ProjectStore`: raw `sqlite3` (WAL, busy_timeout), ~13 tables with FK `ON DELETE CASCADE`. Owns `SCHEMA_SQL` + idempotent migrations (`_migrate_*`). Schema changes must add a migration following the existing `_migrate_*` pattern. `FileStore` manages per-project subdirs (`knowledge`, `attachments`, `snapshots`, `exports`).
- **`engine/*_manager.py`** — async domain logic: `ProjectManager`, `InstructionEngine`, `ChatManager`, `KnowledgeManager`, `AgentBinder`, `RAGCoordinator`. Each defines its own typed exceptions (e.g. `ProjectNotFound`, `ChatNotFound`, `RAGError`) which the transports map to JSON-RPC error codes. Managers are injected, never instantiated ad-hoc in routes.
- **`engine/gateway_client.py`** — the **only** outbound HTTP client. `GatewayClient` fans out to three upstreams with a single shared `httpx.AsyncClient` and carries `Authorization: Bearer` when `FUSION_GATEWAY_API_KEY`/`FUSION_MLX_API_KEY` is set. No circuit breaker (intentionally removed, P1-S1 compliance). `_request` and `artifacts_call` retry 429/5xx/timeout up to 3× with exponential backoff, then raise `GatewayError` (no silent error-dict return) — callers catch it or it maps to JSON-RPC −32011 / HTTP 502. All LLM/RAG/agent calls go through here — upper layers never import httpx directly.

### RPC dispatch (the central registry)

`ProjectRPCServer._handlers` in `daemon_server.py` is a `dict[str, Callable]` mapping every method name (`project.list`, `project.chat.message.add`, `project.rag.query`, `cowork.trigger`, `ping`, `rpc.list`, `tools/list`, …) to an async handler. `dispatch(method, params)` / `handle_request(raw)` do JSON-RPC 2.0 framing and exception→error-code mapping (−32000..−32013 domain errors: −32011 gateway, −32012 chat, −32013 knowledge; −32602 params, −32700 parse). **Adding a new capability = add a handler + register it in this dict.** `rpc.list` / `tools/list` auto-discover from the dict, so the method set is self-documenting.

REST routes (`api/routes.py`) mirror the same managers via FastAPI dependency providers (`get_project_manager`, etc.); SSE chat streaming calls `gateway_client.chat_completions_stream`.

### Upstream wiring

| Upstream | Default URL | Used for |
|---|---|---|
| fusion-gateway | `http://127.0.0.1:11432` | LLM `/v1/chat/completions` (SSE) |
| fusion-rag (fusion-kb) | `http://127.0.0.1:11436` | KB create/upload/search/ask (`/kb/bases/...`) |
| fusion-agent-studio | `http://127.0.0.1:11442` | agent list/get/execute (`/api/v1/agents/...`) |

`start.sh` exports `FUSION_AGENT_STUDIO_URL` defaulting to `11442` to match the fusion 114xx port convention. RAG embedding model defaults to `BAAI--bge-m3`; the RAG KB is lazily created per project and cached as `projects.kb_id`.

### Production hardening (REST)

`AuthMiddleware` (Bearer or `x-api-key`; public paths `/health`, `/docs`, `/openapi.json` exempt), `BodySizeMiddleware` (`FUSION_REST_MAX_BODY_BYTES`), `RateLimitMiddleware` (per-IP). Auth is **off** unless `FUSION_REST_API_KEY` or a `~/.fusion-projects/secret.key` file is present. The UDS socket is `chmod 0o600`. Knowledge upload has path-traversal + size guards.

## Conventions

- 4-space indentation (multiples of 4); no docstrings; every module starts with `logger = logging.getLogger(__name__)`.
- All LLM/RAG/agent I/O goes through `GatewayClient` — no direct MLX/torch/httpx in upper layers.
- `pydantic` models in `models/` validate all RPC/REST payloads; engines raise typed exceptions, transports translate them.
- Tests inject a temp-path `ProjectStore`/`FileStore` via `tests/conftest.py` fixtures — never touch the real `~/.fusion-projects/` data dir. AI/LLM integration tests require real model loading (fusion-mlx on port 11434), no mocks.
- Version is resolved at runtime via `importlib.metadata.version("fusion-project-svc")`; bump it in `pyproject.toml` (and the `rest_server.py` FastAPI title) together. `project_service/__init__.py`'s `__version__ = "0.1.0"` is stale and unused — do not rely on it. See `PLAN.md` for the historical patch-issue workflow.
