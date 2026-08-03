# fusion-project-svc

**English** | [中文](README_CN.md)

Local-first AI **project asset container** service for the Fusion ecosystem. A
*Project* is an isolated work domain that bundles global instructions, a
persistent knowledge base (RAG), isolated chat sessions, and a bound Fusion
Agent. This service owns project metadata, instructions, and storage layout,
and exposes both a UDS JSON-RPC daemon (for Fusion desktop/agent callers) and an
optional REST API.

> **Status: v0.2.1 — Phase 1-3 (local + gateway) + architecture compliance A1-S2.**
> Full project CRUD, instructions + snapshots, knowledge base folders/files,
> chat sessions + fork + move + detach, agent binding, RAG indexing + search,
> audit log, MCP server, and full project export are implemented and green.
> CircuitBreaker + cowork removed (P1-S1 compliance A). RAG chain verified E2E:
> project-svc → fusion-rag → fusion-mlx with BGE-M3 embeddings (score ≥ 0.6).

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
| `project.list` | `{include_archived?, only_starred?}` | `ProjectListItem[]` |
| `project.create` | `ProjectCreate` (name, description?, instructions?, template_id?) | `Project` |
| `project.get` | `{project_id}` | `Project` |
| `project.update` | `{project_id, fields: ProjectUpdate}` | `Project` |
| `project.archive` | `{project_id}` | `Project` |
| `project.unarchive` | `{project_id}` | `Project` |
| `project.star` | `{project_id, starred?}` | `Project` |
| `project.delete` | `{project_id}` | `{deleted: true}` (requires archived) |
| `project.duplicate` | `{project_id, name?}` | `Project` |
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
| `project.rag.query` | `{project_id, query, mode?, folder_ids?, top_k?, threshold?, chat_id?}` | results |
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
`PATCH|DELETE /knowledge/files/{id}`

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
pytest -q          # 94 tests, no LLM/model loading
```

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
