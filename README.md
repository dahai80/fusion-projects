# fusion-project-svc

**English** | [中文](README_CN.md)

Local-first AI **project asset container** service for the Fusion ecosystem. A
*Project* is an isolated work domain that bundles global instructions, a
persistent knowledge base (RAG), isolated chat sessions, and a bound Fusion
Agent. This service owns project metadata, instructions, and storage layout,
and exposes both a UDS JSON-RPC daemon (for Fusion desktop/agent callers) and an
optional REST API.

> **Status: v0.4.3 — release-ops pass: CI (GitHub Actions matrix), deep `/ready` health (dependency fan-out), migration rollback (`migrate.status`/`migrate.down` RPC), auth-off made visible (loud warning not silent), E2E chat verified against live upstreams. Adversarial audit's 42/100 Blocked verdict fully addressed across all four layers + release operations.**
> Full project CRUD, instructions + snapshots, knowledge base folders/files,
> chat sessions + fork + move + detach, agent binding, RAG indexing + search,
> audit log, MCP server, and full project export are implemented and green.
> CircuitBreaker + cowork removed (P1-S1 compliance A). RAG chain verified E2E:
> project-svc → fusion-rag → fusion-mlx with BGE-M3 embeddings (score ≥ 0.6).
> Core-feature acceptance: 76 RPC methods (incl. ping/rpc.list/tools/list
> discovery), 9 MCP tools, 65 REST routes with SSE streaming, 13 SQLite tables
> with FK cascade, 118 tests passing (113 unit + 5 integration).
> **Production hardening (v0.3.0):** REST Bearer/x-api-key auth (public paths
> exempt), per-IP rate limiting, request body size cap, SQLite WAL + busy_timeout,
> UDS socket 0o600, graceful SIGTERM shutdown with client cleanup, secret-file
> key loading, knowledge upload path-traversal + size guards.
> **v0.3.1 patch:** AGENT_STUDIO_URL default 8000→11455 aligned with the fusion
> 114xx port convention (fixes agent binding connectivity, #22); douyin e-commerce
> AgentGraph now fetches product main images + real CDP publish (#23).
> **v0.3.2 patch — E2E assembly (Claude Projects core capability):** chat now
> auto-injects instructions + RAG knowledge into the LLM request at chat time.
> `project.chat.message.stream` UDS RPC + MCP `project_send_message` dispatch to
> it; `build_system_prompt` (agent + instructions) + `rag_coordinator.query`
> assembled into the system message. `template_id` copies instructions + knowledge
> + binding; `project.duplicate` copies knowledge files (disk copy + re-index) +
> optional chats. Compliance: project_manager httpx→GatewayClient,
> ARTIFACTS_URL env-configurable. Verified E2E with real fusion-mlx.
> **v0.3.2 patch (P3 — integration tests):** `tests/test_chat_e2e.py` (5, `@pytest.mark.integration`)
> proves instruction injection, RAG knowledge injection, both combined, rag_mode=OFF
> skip, and chat-history-limit (keeps most-recent N) against a real loaded Qwen3-0.6B-4bit.
> Bug fixes surfaced: `list_messages` now offers `keep_recent=True` (was returning
> oldest N, not newest N for the LLM history window); `knowledge_manager.upload_file`
> re-reads the row after auto-index so the returned `index_status` reflects INDEXED,
> not the stale PENDING.
> **v0.4.0 — adversarial audit fix pass (18 findings, full report `audit-0824.md`):**
> Security — `original_name` path-traversal sanitized (basename + resolve-within-dest
> check) across knowledge upload, project copy, and temp attachments; temp-attachment
> `file_path` now source-validated (`_validate_source`) before copy. IDOR — chat /
> snapshot / message / temp-attachment + RAG folder ops thread `project_id` ownership
> checks (REST boundary enforces; daemon/MCP keep single-user trust). Correctness —
> `restore_snapshot` now actually restores messages (`replace_chat_messages`),
> snapshot stores `messages` + `instruction_snapshot_id` (was dead columns),
> `rag_mode/top_k/threshold` use `is not None` (0 no longer swallowed), `fork_chat`
> batch-inserts with fresh ids. Architecture — `GatewayClient._request` /
> `artifacts_call` retry 429/5xx/timeout with exponential backoff then raise
> `GatewayError` (no more silent error-dict), mapped to JSON-RPC −32011; `daemon` /
> `rest` reuse the injected manager's store (no orphan `ProjectStore()`) and close it
> on shutdown. Perf/maint — rate-limiter bucket eviction (no unbounded `_hits`),
> dead `cowork_tasks` methods + `temp_file_ids` field + duplicate `get()` removed.
> JSON-RPC error codes extended: −32011 gateway, −32012 chat, −32013 knowledge.
> 113 unit/integration tests green; live `chat_completions_stream` re-verified against
> fusion-mlx (Qwen3.5-9B-4bit → PONG). 5 E2E tests need fusion-rag (11436), skipped
> when that upstream is down.
> **v0.4.1 — availability pass (audit-0824 availability layer, 28 findings H/R/E/B):**
> **架构硬伤** — H1 sqlite3 no longer blocks the event loop: hot-path + bulk reads
> offloaded via `asyncio.to_thread` (store is thread-safe under its `RLock`; sub-ms
> PK lookups stay sync). H2 transport drift closed: REST SSE chat now mirrors daemon
> (model/temperature/max_tokens honored, `isinstance` RAG guard, ownership checks);
> both surfaces share the injected manager store. H3 per-upstream isolated
> `httpx.AsyncClient` pools (`httpx.Limits`, `GATEWAY_POOL_MAX_CONN/KEEPALIVE`) so a
> long-lived gateway stream cannot starve RAG/agent/artifacts. H4 KB-id cache
> self-heals: `_ensure_kb` probes on use, clears stale `kb_id` on 404 and rebuilds.
> H5 real UDS streaming: `stream.delta` JSON-RPC notification frames emitted as
> tokens arrive (was full-collect-then-return). H6 state reconciliation: delete/
> replace chain `remove_file_index` + disk `unlink`; `delete_project` calls
> `rag_delete_kb`; `delete_chat`/`delete_temp_attachment` unlink attachments dir.
> H7 MCP split-brain removed: REST-mounted MCP reuses `app.state` managers/store
> (MCP stdio routes through the daemon, no second `ProjectStore()`).
> **运行时风险** — R1 multi-folder RAG query (per-folder `gather` + merge, was
> silent first-only). R3 SSE client disconnect cancels upstream LLM stream.
> R4/R5 dead `agent_execute` + zombie `cowork.trigger/status` removed from the
> handler registry (`rpc.list` no longer advertises them). R6 RAG context fenced
> with `<retrieved_document>` + untrusted-content instruction. R7 UDS request-line
> byte cap (`UDS_MAX_LINE_BYTES`, over-limit disconnects). R8 export/duplicate
> offloaded to a thread (no event-loop-blocking O(N) loops).
> **工程实现缺陷** — E1 per-project + global disk quota (`check_quota` at upload/
> replace, 507 on REST, −32014 on RPC). E2 `wal_autocheckpoint` + periodic
> `wal_checkpoint(TRUNCATE)`. E3 `RotatingFileHandler` (`LOG_MAX_BYTES`/`BACKUP_COUNT`).
> E4 export streamed in chunks over UDS (`project.export.stream`, inline path capped
> at `EXPORT_INLINE_MAX_BYTES`). E5 rate limiter honors `X-Forwarded-For` + IP-cap
> eviction (`RATE_MAX_IPS`). E6 PID file `flock` + process-identity check
> (`_pid_is_daemon`, no stale-PID reuse). E7 temp attachments cleaned on chat delete.
> **并发** — chat stream runs agent-prompt + RAG-query concurrently (`asyncio.gather`);
> upstream health checks fan out; `index_folder` indexes files concurrently under a
> semaphore with a per-project KB-creation lock (no duplicate KB).
> 125 tests passing (121 unit + 4 new H3 fan-out; 5 integration skipped when
> fusion-rag/11436 down).

> **v0.4.2 — residual audit re-verification (4 findings the v0.4.1 declaration
> missed, found by re-checking every audit item against current code):**
> **H6-item4** — `set_binding` now validates the agent exists upstream before
> storing (`get_agent_preview` returns None on missing/empty upstream response →
> raises `AgentUnavailable`, a previously-dead exception). `get_agent_preview` is
> now None-safe (was `AttributeError` on falsy upstream return). Daemon maps
> `AgentUnavailable` → −32008; REST `set_agent_binding` catches `AgentBinderError`
> → 400 (was uncaught → 500).
> **R4** — dead `GatewayClient.agent_execute` method removed (zero callers;
> v0.4.1 README claimed removal but the method survived).
> **MCP domain-error mapping (H2 drift point 3)** — `tools/call` no longer bare
> stringifies domain exceptions. `ProjectNotFound`/`ChatNotFound`/
> `FolderNotFound`/`KnowledgeFileNotFound`/`KnowledgeQuotaExceeded`/
> `KnowledgeError`/`AgentUnavailable`/`AgentBinderError`/`RAGError`/`GatewayError`/
> `ProjectError` map to typed `isError` results with matching code prefixes
> (`_MCP_DOMAIN_CODES` table mirrors the daemon error-code registry).
> **MCP stdio byte cap (R7)** — `run_mcp_stdio` now caps each request line at
> `MCP_MAX_LINE_BYTES` (env `FUSION_MCP_MAX_LINE_BYTES`, default 16MB, mirrors
> `UDS_MAX_LINE_BYTES`); oversize lines get a −32604 "request too large" error
> and the loop continues (was unbounded — same attack surface as the pre-fix UDS
> reader).
> 133 tests passing (125 prior + 8 new in `tests/test_mcp_server.py`; 5
> integration skipped when upstreams down).

## Layout

```
fusion-projects/
├── pyproject.toml
├── start.sh                     # start|stop|restart|status for the UDS daemon
├── project_service/
│   ├── __init__.py
│   ├── config.py                # paths, ports, URLs, defaults (env-overridable)
│   ├── client.py                # ProjectClient - UDS JSON-RPC client + RPCError
│   ├── daemon_server.py         # ProjectRPCServer - UDS JSON-RPC 2.0 daemon
│   ├── mcp_server.py            # MCP JSON-RPC server (Claude/Cursor integration)
│   ├── models/
│   │   ├── project.py           # ProjectCreate/ProjectUpdate/Project/ProjectListItem
│   │   ├── instruction.py       # InstructionContent/InstructionSave/InstructionSnapshot
│   │   ├── chat.py              # Chat/ChatCreate/ChatUpdate/Message/TempAttachment
│   │   ├── knowledge.py         # KnowledgeFolder/KnowledgeFile/FolderCreate/FolderUpdate
│   │   ├── agent_binding.py     # AgentBinding/AgentMeta/AgentPreview/PromptMergeMode
│   │   ├── artifact_ref.py      # ArtifactRef/ArtifactMigrateRequest
│   │   ├── audit.py             # AuditLogEntry
│   └── (removed — cowork migrated to fusion-cowork)
│   ├── store/
│   │   ├── project_store.py     # raw sqlite3 ProjectStore (15 tables, no ORM)
│   │   └── file_store.py        # per-project storage dirs
│   ├── engine/
│   │   ├── project_manager.py   # async ProjectManager + domain exceptions
│   │   ├── instruction_engine.py# async InstructionEngine + snapshot restore/delete
│   │   ├── chat_manager.py      # async ChatManager + move/detach/temp-attachments
│   │   ├── knowledge_manager.py # async KnowledgeManager + file upload/replace
│   │   ├── agent_binder.py      # async AgentBinder + upstream agent resolution
│   │   ├── rag_coordinator.py   # async RAGCoordinator (delegates to fusion-rag)
│   │   ├── gateway_client.py     # lightweight GatewayClient (no circuit breaker)
│   │   └── (removed — upstream_client migrated to gateway_client)
│   └── api/
│       ├── routes.py            # FastAPI /v1 router + MCP endpoint
│       └── rest_server.py       # create_app() + uvicorn entry
└── tests/                       # pytest, asyncio_mode=auto
```

## Install

```bash
cd ~/fusion/fusion-projects
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

## Run

### UDS daemon (primary - launched by `start.sh`)

```bash
./start.sh start    # listens on /tmp/fusion-project-svc.sock (chmod 0o666)
./start.sh status
./start.sh stop
./start.sh restart
```

Logs: `logs/stdout.log`, `logs/stderr.log`. PID: `.fusion-project-svc.pid`.

### REST API (optional)

```bash
python -m project_service.rest_server   # http://127.0.0.1:11440
```

### MCP server (stdio mode, for Claude/Cursor)

```bash
python -m project_service.mcp_server    # communicates on stdin/stdout
```

## RPC methods (UDS, JSON-RPC 2.0)

### Project

| Method | Params | Returns |
|---|---|---|
| `project.list` | `{include_archived?, only_starred?, limit?, offset?}` | `ProjectListItem[]` |
| `project.create` | `ProjectCreate` (name, description?, instructions?, template_id?) | `Project` (template_id copies instructions + knowledge folders/files + agent binding from template) |
| `project.get` | `{project_id}` | `Project` |
| `project.update` | `{project_id, fields: ProjectUpdate}` | `Project` |
| `project.archive` | `{project_id}` | `Project` |
| `project.unarchive` | `{project_id}` | `Project` |
| `project.star` | `{project_id, starred?}` | `Project` |
| `project.delete` | `{project_id}` | `{deleted: true}` (requires archived) |
| `project.duplicate` | `{project_id, name?, copy_chats?}` | `Project` (copies instructions + knowledge folders/files w/ disk files + re-index + agent binding; copy_chats also copies chats & messages) |
| `project.export` | `{project_id}` | `{zip_base64, size}` |
| `project.artifact.list` | `{project_id, artifact_type?, limit?, offset?}` | `ArtifactRef[]` |
| `project.artifact.migrate` | `ArtifactMigrateRequest` | `ArtifactRef` |
| `project.artifact.export` | `{project_id, artifact_ids?}` | zip bytes |

### Instructions

| Method | Params | Returns |
|---|---|---|
| `project.instruction.get` | `{project_id}` | `InstructionContent` |
| `project.instruction.save` | `{project_id, content}` | `InstructionContent` |
| `project.instruction.clear` | `{project_id}` | `{cleared: bool}` |
| `project.instruction.snapshots` | `{project_id}` | `InstructionSnapshot[]` |
| `project.instruction.snapshot.restore` | `{snapshot_id}` | `InstructionContent` |
| `project.instruction.snapshot.delete` | `{snapshot_id}` | `{deleted: true}` |

### Chat

| Method | Params | Returns |
|---|---|---|
| `project.chat.list` | `{project_id, only_starred?}` | `ChatListItem[]` |
| `project.chat.create` | `{project_id, title?}` | `Chat` |
| `project.chat.get` | `{chat_id}` | `Chat` |
| `project.chat.update` | `{chat_id, fields}` | `Chat` |
| `project.chat.delete` | `{chat_id}` | `{deleted: true}` |
| `project.chat.star` | `{chat_id, starred?}` | `Chat` |
| `project.chat.fork` | `{chat_id, label?}` | `Chat` |
| `project.chat.move` | `{chat_id, target_project_id}` | `Chat` |
| `project.chat.detach` | `{chat_id}` | `Chat` (project_id=null) |
| `project.chat.message.add` | `{chat_id, content, role?}` | `Message` |
| `project.chat.message.list` | `{chat_id, limit?, offset?}` | `{messages, total}` |
| `project.chat.message.stream` | `{chat_id, content, role?, rag_mode?, rag_scope?, model?, temperature?, max_tokens?}` | `{message: Message, project_id}` (non-streaming full reply; auto-injects instructions + RAG context, see E2E assembly) |
| `project.chat.temp_attachment.add` | `{chat_id, file_path, original_name, file_size, mime_type?}` | `TempAttachment` |
| `project.chat.temp_attachment.list` | `{chat_id}` | `TempAttachment[]` |
| `project.chat.temp_attachment.delete` | `{attachment_id}` | `{deleted: true}` |

### Knowledge

| Method | Params | Returns |
|---|---|---|
| `project.knowledge.folder.list` | `{project_id}` | `KnowledgeFolder[]` |
| `project.knowledge.folder.create` | `{project_id, name, parent_id?}` | `KnowledgeFolder` |
| `project.knowledge.folder.update` | `{folder_id, name?}` | `KnowledgeFolder` |
| `project.knowledge.folder.delete` | `{folder_id}` | `{deleted: true}` |
| `project.knowledge.file.list` | `{project_id, folder_id?}` | `KnowledgeFile[]` |
| `project.knowledge.file.upload` | `{project_id, source_path, original_name, folder_id?, mime_type?}` | `KnowledgeFile` |
| `project.knowledge.file.replace` | `{file_id, source_path}` | `KnowledgeFile` |
| `project.knowledge.file.update` | `{file_id, name?, folder_id?}` | `KnowledgeFile` |
| `project.knowledge.file.delete` | `{file_id}` | `{deleted: true}` |
| `project.knowledge.file.always_include` | `{file_id, always_include?}` | `KnowledgeFile` |

### Agent binding

| Method | Params | Returns |
|---|---|---|
| `project.agent.set` | `{project_id, agent_id, merge_mode?}` | `AgentBinding` |
| `project.agent.get` | `{project_id, chat_id?}` | `AgentBinding` |
| `project.agent.preview` | `{project_id}` | `AgentPreview` |
| `project.agent.remove` | `{binding_id}` | `{deleted: true}` |

### RAG

| Method | Params | Returns |
|---|---|---|
| `project.rag.index` | `{project_id, folder_id}` | `{indexed, results}` |
| `project.rag.query` | `{project_id, query, mode?, folder_ids?, top_k?, threshold?, chat_id?}` | `{results, sources, mode}` |
| `project.rag.index.remove` | `{file_id}` | `{removed: true}` |
| `project.rag.status` | `{project_id}` | status |
| `project.rag.config.get` | `{project_id}` | `{rag_mode, rag_top_k, rag_threshold}` |
| `project.rag.config.set` | `{project_id, rag_mode?, rag_top_k?, rag_threshold?}` | config |

### Audit

| Method | Params | Returns |
|---|---|---|
| `project.audit.list` | `{project_id, limit?, offset?}` | `AuditLogEntry[]` |
| `project.audit.log` | `{project_id, action, chat_id?, agent_id?, details?}` | `AuditLogEntry` |

Error codes: `-32700` parse, `-32601` method not found, `-32602` invalid/missing
params, `-32000` project generic, `-32001` project not found, `-32002` not
archived, `-32005` chat not found, `-32006` folder not found, `-32007` knowledge
file not found, `-32008` agent binder error, `-32009` RAG error, `-32010`
snapshot not found, `-32603` internal.

### Client (Python)

```python
import asyncio
from project_service.client import ProjectClient

async def main():
    c = ProjectClient()                       # defaults to /tmp/fusion-project-svc.sock
    p = await c.create_project(name="My Project", description="...")
    await c.save_instruction(p["id"], "Be concise and cite sources.")
    print(await c.list_projects())

asyncio.run(main())
```

## REST endpoints (`/v1`)

Project: `GET /projects` · `POST /projects` · `GET|PATCH /projects/{id}` ·
`POST /projects/{id}/archive|unarchive|star|duplicate|export` ·
`DELETE /projects/{id}`

Instructions: `GET|PUT|DELETE /projects/{id}/instructions` ·
`GET /projects/{id}/instructions/snapshots` ·
`POST /projects/{id}/instructions/snapshots/{sid}/restore|delete`

Chat: `GET /projects/{id}/chats` · `POST /projects/{id}/chats` ·
`GET|PATCH|DELETE /chats/{id}` · `POST /chats/{id}/star|fork|move|detach` ·
`GET /chats/{id}/messages` · `POST /chats/{id}/messages` ·
`POST /chats/{id}/messages/stream` (SSE) ·
`GET|POST|DELETE /chats/{id}/temp-attachments`

Knowledge: `GET /projects/{id}/knowledge/folders` ·
`POST|PATCH|DELETE /knowledge/folders/{id}` ·
`GET /projects/{id}/knowledge/files` ·
`POST /projects/{id}/knowledge/files/upload|replace` ·
`PATCH|DELETE /knowledge/files/{id}` ·
`POST /projects/{id}/knowledge/files/{file_id}/always-include`

Agent: `POST /projects/{id}/agent` · `GET /projects/{id}/agent` ·
`DELETE /projects/{id}/agent` · `POST /projects/{id}/system-prompt`

RAG: `POST /projects/{id}/rag/index|query` ·
`DELETE /projects/{id}/rag/index/{file_id}` ·
`GET /projects/{id}/rag/status|config` · `PUT /projects/{id}/rag/config`

Artifacts: `GET /projects/{id}/artifacts` ·
`POST /projects/{id}/artifacts/migrate|export`

Audit: `GET|POST /projects/{id}/audit`

MCP: `POST /mcp` (JSON-RPC 2.0 with tools/list, tools/call, initialize)

## MCP tools (for Claude/Cursor)

| Tool | Description |
|---|---|
| `project_list` | List all projects |
| `project_get` | Get project details |
| `project_search_knowledge` | Search project knowledge base via RAG |
| `project_list_knowledge` | List knowledge files in a project |
| `project_get_instructions` | Get project instructions |
| `project_list_chats` | List chats in a project |
| `project_get_chat_messages` | Get messages from a chat |

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `FUSION_PROJECT_SOCK` | `/tmp/fusion-project-svc.sock` | UDS socket path |
| `FUSION_PROJECT_HOST` | `127.0.0.1` | REST host |
| `FUSION_PROJECT_PORT` | `11440` | REST port |
| `FUSION_PROJECT_HOME` | `~/.fusion-projects` | data + storage root |
| `FUSION_MLX_URL` | `http://127.0.0.1:11434/v1` | fusion-mlx base URL |
| `FUSION_MLX_API_KEY` | `""` | fusion-mlx Bearer token |
| `FUSION_RAG_URL` | `http://127.0.0.1:11436` | fusion-rag/kb base URL |
| `FUSION_RAG_EMBEDDING_MODEL` | `BAAI--bge-m3` | embedding model ID for KB creation |
| `FUSION_AGENT_STUDIO_URL` | `http://127.0.0.1:8000` | agent-studio URL |
| `FUSION_GATEWAY_URL` | `http://127.0.0.1:8100` | fusion-gateway URL |
| `FUSION_GATEWAY_URL` | `http://127.0.0.1:11432` | fusion-gateway base URL

## RAG E2E chain

The project service integrates with fusion-rag (port 11436) and fusion-mlx (port
11434) for knowledge base indexing and retrieval. The verified flow:

```
project-svc (UDS RPC)
  → rag_coordinator.index_file() / .query()
    → gateway_client.rag_upload_doc() / .rag_search()
      → fusion-rag /kb/bases/{kb_id}/documents (upload + embed)
        → fusion-mlx /api/v1/embeddings (BAAI--bge-m3, 1024-dim)
      → fusion-rag /kb/bases/{kb_id}/search (vector similarity)
```

**Prerequisites:**
- fusion-mlx running on port 11434 with BGE-M3 model loaded
- fusion-rag running on port 11436 with `mlx_api_key` and `embedding_model='BAAI--bge-m3'`
- project-svc started with `FUSION_MLX_API_KEY=dahai168 FUSION_RAG_EMBEDDING_MODEL=BAAI--bge-m3`

**Verified E2E sequence:**
1. `project.create` → project with `kb_id: null`
2. `project.knowledge.folder.create` → folder for docs
3. `project.knowledge.file.upload` → copies file to project storage, `index_status: PENDING`
4. `project.rag.index_file` → calls fusion-rag upload → creates KB on first use → stores `kb_id` → returns `doc_id, chunks, chars`
5. `project.rag.query` → calls fusion-rag search → returns results with `score ≥ 0.6`

## E2E chat assembly (Claude Projects core)

The three pillars — Instructions, Files (RAG knowledge), Chat history — are
assembled into the LLM request at chat time. Both transports do the same
assembly:

- **REST** `POST /chats/{id}/messages/stream` (SSE) — streams tokens live.
- **UDS** `project.chat.message.stream` — waits for the full reply, returns
  `{message, project_id}` in one JSON-RPC result.
- **MCP** `project_send_message` — dispatches to the UDS stream handler.

Assembly (per request):

```
system_content = build_system_prompt(project_id, chat_id)   # agent prompt + instructions (PromptMergeMode)
              ++ _format_rag_context(rag_coordinator.query(...))   # only when rag_mode != OFF
llm_messages   = [system] + history(limit=FUSION_CHAT_HISTORY_LIMIT)
                → gateway_client.chat_completions_stream(llm_messages, ...)
```

`rag_mode` (`AUTO` default / `MANUAL` / `OFF`) and `rag_scope` (folder id list)
per message. Verified E2E: a project with instruction *"always start your reply
with BANANA"* → `project.chat.message.stream` → assistant replies `BANANA.`,
proving instructions are injected into the system message and obeyed by the
real model (fusion-mlx via fusion-gateway).

## Storage layout

```
~/.fusion-projects/
├── data/projects.db                              # SQLite (15 tables)
└── storage/{project_id}/{knowledge,attachments,snapshots,exports}/
```

SQLite tables: `projects`, `instructions`, `instruction_snapshots`,
`project_artifacts`, `chats`, `chat_snapshots`, `messages`,
`knowledge_folders`, `knowledge_files`, `chat_agent_bindings`, `rag_queries`,
`temp_attachments`, `audit_log`,  Dates are ISO-8601 UTC; IDs
are `uuid4().hex`. Foreign keys cascade on project delete.

## Business rules

- **Delete requires archive:** `project.delete` / `DELETE /projects/{id}` returns
  error `-32002` / HTTP `409` unless the project is archived first.
- **Instruction length:** capped at `MAX_INSTRUCTION_CHARS` (10000).
- **Instruction snapshots:** saving a changed instruction auto-snapshots the
  previous content (label `auto`). Snapshots can be restored or deleted.
- **Chat project_id nullable:** chats can be detached from a project (project_id=NULL).
- **Full project export:** creates a ZIP with project.json, instructions.json,
  chats + messages, knowledge folders/files, agent bindings.
- **Cowork bridge:** triggers automation tasks (pending → running → done/failed)
  with results stored in SQLite.

## Tests

```bash
source .venv/bin/activate
pytest -q                       # 113 unit tests, no LLM/model loading
pytest -q -m "not integration"  # unit only (CI default tier)
pytest -q tests/test_chat_e2e.py # 5 integration tests (need live upstreams)
```

Two tiers via the `integration` marker (pyproject.toml):
- **Unit** (113): tmp_path stores, never touch `~/.fusion-projects` or load models.
- **Integration** (5, `tests/test_chat_e2e.py`): real model end-to-end. Requires
  fusion-mlx (11434, key `dahai168`, model `Qwen3-0.6B-4bit`), fusion-rag (11436,
  `FUSION_MLX_URL=http://127.0.0.1:11434/v1`), and fusion-gateway (11432) all up.
  Auto-skip via `_upstreams_up()` health probe if any is down. The fixture points
  LLM direct to fusion-mlx (11434) to run Qwen3 locally and avoid gateway
  cloud-routing 502; still a real loaded model, no mocks.

Coverage: `ProjectStore` CRUD/filters/cascade, `ProjectManager` lifecycle +
archive-before-delete + duplicate + export, `InstructionEngine` save/snapshot/
restore/delete + length validation, `ChatManager` move/detach/temp-attachments,
`KnowledgeManager` folder/file CRUD + upload/replace, `AgentBinder` set/get/
preview, `RAGCoordinator` config, `CoworkBridge` trigger/status, `MCPServer`
initialize/tools-list/parse-error, UDS `ProjectRPCServer` end-to-end via
`ProjectClient`, REST API via `httpx.ASGITransport`. All tests use `tmp_path` -
the real `~/.fusion-projects` is never touched.

## Conventions

- Raw `sqlite3` with `sqlite3.Row` (no ORM), `@contextmanager` cursor with
  commit/rollback + `threading.RLock`.
- UDS JSON-RPC daemon hand-rolled on `asyncio.start_unix_server` (no RPC library)
- MCP server follows the 2024-11-05 protocol spec (initialize → tools/list →
  tools/call). Available via `POST /api/v1/mcp` (HTTP) or stdio (CLI).
- `logger = logging.getLogger(__name__)` per module; `logging.basicConfig` only in
  entry points.
- 4-space indentation, no docstrings.

## Release operations (v0.4.3)

### CI
`.github/workflows/ci.yml` runs on push (branches `master`, `fix/**`) and pull
request to `master`. The **test** job runs `pytest tests/ -v` on a Python 3.11
+ 3.12 matrix and uploads the report artifact; the **build** job (needs test)
runs `python -m build` and uploads the `dist/` artifact. No lint job — this
project defines no ruff config (do not invent one).

### Health checks
- `GET /health` — liveness, always 200 `{"status":"ok"}` (no dependency probe).
- `GET /ready` — **readiness**. Fans out to all three upstreams concurrently
  (`asyncio.gather`): `gateway_is_healthy` (11432), `rag_is_healthy` (11436),
  `agent_studio_is_healthy` (11455). Returns `200 {"status":"ready","deps":{...}}`
  when all reachable, else `503 {"status":"degraded","deps":{...}}` naming which
  dependency is down. `/ready` is a public path (auth-exempt) so load balancers
  and orchestrators can probe it without credentials.

### Deploy
```bash
cd ~/fusion/fusion-projects
source .venv/bin/activate
pip install -e ".[test]"          # refresh install after a pull
./start.sh restart                 # UDS daemon
curl -s http://127.0.0.1:11440/ready   # confirm deps green before serving traffic
```
Logs: the daemon rotates `~/.fusion-projects/logs/stdout.log` in-process
(`FUSION_LOG_MAX_BYTES` default 50 MiB, `FUSION_LOG_BACKUP_COUNT` default 5).
Set `FUSION_LOG_JSON=1` to emit one-line JSON records (carrying `tenant_id`/
`user_id` when a tenant context is active) instead of the default text
formatter. `start.sh` writes only pre-handler boot output to `logs/boot.log`
(project-local); runtime logs live under the data dir, not next to `start.sh`.

REST API auth is **off by default**. Two guards:
- **Fail-fast bind guard**: if `FUSION_REST_API_KEY` is unset, `FUSION_REST_ALLOW_NO_AUTH`
  is not set, **and** `FUSION_PROJECT_HOST` is a non-loopback address (e.g. `0.0.0.0`),
  the REST server refuses to start (`RuntimeError`). Loopback binds (default
  `127.0.0.1`) are allowed unauthenticated — single-user local-first is the design intent.
- For any deployment beyond single-user localhost, set a key before start:
```bash
export FUSION_REST_API_KEY="$(openssl rand -hex 32)"
./start.sh restart
```
If you intentionally run unauthenticated (local dev only), acknowledge it so the
startup warning downgrades from CRITICAL to a one-time WARNING:
```bash
export FUSION_REST_ALLOW_NO_AUTH=1
```

### Observability
- `GET /metrics` — public (auth-exempt) JSON snapshot of in-process counters:
  `total_requests`, `requests_by_status`, `rate_limit_rejected`, `auth_rejected`,
  `body_oversize_rejected`, live `upstream_health` (gateway/rag/agent-studio),
  `auth` state, the active `rate_limit` (limit + window), and RAG recall
  quality (`rag_query_total`, `rag_avg_recall`, `rag_zero_recall`,
  `rag_below_threshold`). No Prometheus dependency — poll with `curl` or
  scrape into any collector.
- Rate limit default: **60 requests / 60 s per IP** (`FUSION_REST_RATE_LIMIT`,
  `FUSION_REST_RATE_WINDOW`), tuned for production. Override for higher throughput.
- Per-module `logging` at INFO; `start.sh` / `rest_server.main()` attach
  `RotatingFileHandler` to the root logger.

### Schema migration + rollback
`ProjectStore` tracks schema via `PRAGMA user_version`; `SCHEMA_VERSION` is the
latest. Every additive migration must add a matching `_ROLLBACK_SQL[v]` entry.

Check current state (UDS RPC, or `store.migrate_status()` directly):
```jsonc
// rpc: migrate.status
{"current_version": 2, "latest_version": 2, "rollbackable_versions": [2, 1]}
```

Roll back to a prior version (destructive — drops additive columns):
```jsonc
// rpc: migrate.down
{"target_version": 1, "confirm": "rollback-schema"}
```
`confirm` must equal the literal `rollback-schema` to acknowledge the destructive
schema change. Invalid targets (negative, or > current) raise `ValueError`. A
forward re-run of `ProjectStore` initialization re-applies the up-migrations.

## Known constraints (not defects)

These are deliberate design boundaries or tracked-upstream items, not bugs in
this service:

- **Single-node, local-first** — one UDS daemon + one SQLite DB, no horizontal
  scaling, no clustering. This matches the Fusion "一核九端" local-first
  architecture: this service is a per-machine project asset container, not a
  multi-tenant cloud service. Scaling out is out of scope; if a deployment needs
  it, run one instance per node.
- **Upstream fusion-mlx `DraftModelDecoder.config` 500** — chat completion with
  model `Qwen3-0.6B-4bit` returns HTTP 500 from fusion-mlx (speculative-decoding
  draft model missing `.config`). Tracked upstream: **dahai80/fusion-mlx#623**
  (issue → PR → land per monorepo rule; not fixed here). Workaround: use a
  confirmed-loaded chat model (`Qwen3.5-4B-bf16`, `Qwen3.5-9B-4bit`,
  `Qwen3.8-27B-4bit`); `GatewayClient` surfaces upstream 5xx as `GatewayError`
  → JSON-RPC −32011 / HTTP 502, so callers see a clear error, not a silent hang.

## Changelog

### v0.5.2 — Structured logging, tenant-level rate limiting
Observability + multi-tenant hardening from the benchmark backlog
(`insight/fusion-projects-insight-0903.md` §5.4 / §5.1):
- **Structured JSON logs (opt-in)**: `project_service/logging_config.py`
  centralizes log setup with `RotatingFileHandler` (both daemon + REST
  entrypoints). Set `FUSION_LOG_JSON=1` for one-line JSON records carrying
  `ts`/`level`/`name`/`msg` + `tenant_id`/`user_id` when a tenant context is
  active — machine-parseable, no Prometheus dep. Default stays the legacy
  text formatter.
- **Tenant-level rate limiting**: `RateLimitMiddleware` now keys the limiter
  on `tenant:<id>` when a tenant context is present (multi-tenant identity
  mode), instead of client IP. Single-user mode is unchanged (IP-keyed).
  One noisy tenant can no longer exhaust the shared per-IP bucket.
- Upstream: filed `fusion-rag#70` for rerank (bge-reranker-v2-m3) + hybrid
  retrieval (BM25 + vector) — the P1 recall-quality gap that lives in the
  fusion-rag retrieval pipeline, not in this service.

### v0.5.1 — RAG citations, always-include, pagination, recall metrics
Benchmark-driven pass against Claude Projects (see `insight/fusion-projects-insight-0903.md`).
Closes the RAG-traceability and recall-observability gaps:
- **RAG citations (sources回传)**: `project.rag.query` now returns a
  `sources` array (`file_id`/`file_name`/`doc_id`/`score`/`snippet`) for
  every recalled chunk. The SSE chat `done` event carries `sources` so the
  frontend can render clickable citations (Claude-Projects-equivalent).
  Sources are persisted to `messages.rag_sources` for post-hoc tracing.
- **Always-include files (绕过召回)**: `knowledge_files.always_include`
  column (schema v4). Marked files are injected **in full** into the system
  message, bypassing top_k/threshold recall — eliminates "key file not
  recalled" failures. Toggle via RPC `project.knowledge.file.always_include`
  and REST `POST /knowledge/files/{id}/always-include`.
- **list_projects pagination**: `limit`/`offset` (default 50) on store,
  `ProjectManager.list`, RPC `project.list`, and REST `GET /projects`.
- **Recall-quality metrics**: `metrics.record_rag_query` tracks
  `rag_query_total`, `rag_avg_recall`, `rag_zero_recall`,
  `rag_below_threshold` — surfaced in `/metrics`. Makes recall quality
  observable and tunable (no longer a black box).

### v0.5.0 — fusion-identity multi-tenant integration
- **Multi-tenant isolation**: REST surface is now identity-gated when
  `FUSION_IDENTITY_SERVICE_TOKEN` is set. fusion-core `TenantMiddleware`
  (fail-closed) replaces the legacy global-key `AuthMiddleware`: every
  non-exempt request requires `X-Tenant-Id`; a Bearer JWT is verified
  against fusion-identity `POST /api/v1/auth/verify`; `jwt.tid` must match
  the header or the request is rejected with 401.
- **Three red lines enforced**: (1) fail-closed — missing token/header →
  401; (2) cross-tenant denied — `tid ↔ header ↔ row.tenant_id` mismatch
  → 404/None; (3) data-isolation — `tenant_id TEXT NOT NULL DEFAULT ''`
  column on `projects` (schema v2 → v3, `_migrate_tenant_id` migration +
  rollback entry), stamped from `TenantContext` on create, filtered on
  list, guarded on get/update/delete at the store layer (single chokepoint
  covering all five managers).
- **UDS daemon unchanged**: single-user trust model preserved — no tenant
  middleware on the daemon; store enforcement is a no-op when no
  `TenantContext` is set (contextvar empty).
- **Usage reporting**: LLM token usage emitted to fusion-identity
  `POST /api/v1/tenants/{tid}/usage` when `FUSION_IDENTITY_USAGE_REPORT=1`
  and a tenant context is present.
- **Config**: `FUSION_IDENTITY_URL` (default `http://127.0.0.1:11470`),
  `FUSION_IDENTITY_SERVICE_TOKEN`, `FUSION_IDENTITY_VERIFY_TIMEOUT`,
  `FUSION_IDENTITY_USAGE_REPORT`.
- **Backward compatible**: with no identity token configured, REST keeps
  the existing `AuthMiddleware` global-key gate — offline tests and
  single-user deployments are unaffected. 153 tests green (143 prior +
  10 new tenant-isolation tests).

### v0.4.5 — Python 3.12 import crash fix (CI green)
- **Production blocker fixed**: `ProjectManager` defines `async def list(...)`,
  shadowing the builtin `list` in the class namespace. The annotations
  `-> list[ProjectListItem]` and `-> list[ArtifactRef]` are evaluated eagerly at
  class-definition time on Python 3.11/3.12, resolving `list` to the method
  object → `TypeError: 'function' object is not subscriptable` → service fails
  to import on the declared 3.12 runtime → all tests fail to collect → CI red
  on every run since the workflow landed in v0.4.3.
- **Why local stayed green**: repo venv ran Python 3.14 (lazy annotations,
  PEP 649), masking the shadow. Monorepo `.python-version` pins 3.12 — the
  production target.
- **Fix**: `from __future__ import annotations` (postponed evaluation). Single
  line, behavior-preserving, no caller change.
- **Verified on Python 3.12.14** (clean venv matching the CI matrix): 148
  passing (143 unit + 5 E2E integration). CI green for the first time.

### v0.4.4 — residual risk pass
- **Auth-off bind guard**: REST server refuses to start (fail-fast `RuntimeError`)
  when auth is off, unacknowledged, **and** bound to a non-loopback host. Loopback
  stays allowed (local-first single-user). `config.rest_host_is_loopback()` helper.
- **Log rotation conflict fixed**: `start.sh` no longer redirects to the same
  `stdout.log` the in-process `RotatingFileHandler` owns (inode divergence on
  rotation). Shell redirect now captures only pre-handler boot output to
  `logs/boot.log`; runtime logs rotate under `~/.fusion-projects/logs/`.
- **Portable start lock**: replaced `flock` (absent on macOS) with a `mkdir`-based
  atomic lock, released on start success/failure and on stop.
- **Rate-limit default tightened**: `FUSION_REST_RATE_LIMIT` 120 → 60 per 60 s
  (production-safe, still generous for UI use).
- **`GET /metrics`**: public JSON endpoint exposing request/status counters,
  rate-limit/auth/body rejections, live upstream health, auth state, rate config.
  New `project_service/metrics.py` (in-process, thread-safe, no Prometheus dep).
- 148 tests passing (143 unit + 5 E2E integration).

### v0.4.3 — release-ops pass
- **CI** (`.github/workflows/ci.yml`): Python 3.11/3.12 test matrix + `python -m
  build` job; runs on push to `master`/`fix/**` and PRs to `master`.
- **REST `/ready`**: deep readiness probe — concurrent fan-out to gateway/rag/
  agent-studio, `200` ready / `503` degraded with per-dependency status.
- **Migration rollback**: `migrate.status` + `migrate.down` RPC handlers; store
  `migrate_down(target)` with `_ROLLBACK_SQL` per-version SQL, `confirm` guard.
- **Auth-off visibility**: when `REST_API_KEY` unset and `FUSION_REST_ALLOW_NO_AUTH`
  not set, `AuthMiddleware` logs CRITICAL `UNAUTHENTICATED` on every request;
  acknowledged mode warns once. `REST_ALLOW_NO_AUTH` config knob added.
- **E2E chat verified**: 5 integration tests run against live fusion-mlx (11434),
  fusion-rag (11436), fusion-gateway (11432) — instruction injection, RAG
  knowledge injection, instruction+RAG, RAG-off skip, history limit all green.
  E2E model switched to `Qwen3.5-4B-bf16` (env `FUSION_E2E_MODEL`); the prior
  `Qwen3-0.6B-4bit` hit upstream fusion-mlx `DraftModelDecoder.config` bug
  (filed dahai80/fusion-mlx#623).
- 145 tests passing (140 unit + 5 E2E integration).

### v0.4.2 — residual audit re-verify
- H6-item4: `AgentBinder.set_binding` validates agent exists upstream before
  storing (rejects binding to a missing agent → `AgentUnavailable`).
- R4: removed dead `GatewayClient.agent_execute` (zero callers).
- MCP `tools/call` maps domain exceptions to typed `isError` results via
  `_MCP_DOMAIN_CODES` (mirrors the daemon error registry).
- MCP stdio loop enforces `FUSION_MCP_MAX_LINE_BYTES` cap (default 16 MiB),
  replies `-32604` on oversized requests instead of crashing.
- 133 tests passing.

