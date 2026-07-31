# fusion-project-svc

Local-first AI **project asset container** service for the Fusion ecosystem. A
*Project* is an isolated work domain that bundles global instructions, a
persistent knowledge base (RAG), isolated chat sessions, and a bound Fusion
Agent. This service owns project metadata, instructions, and storage layout,
and exposes both a UDS JSON-RPC daemon (for Fusion desktop/agent callers) and an
optional REST API.

> **Status: Phase 0 (scaffold).** Project CRUD, instruction management, SQLite
> store, UDS daemon, REST mirror, and tests are implemented and green. Knowledge
> base / RAG / chat / agent binding land in later phases - see the architecture
> doc at `~/fusion/architecture/fusion-projects-ar.md`.

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
│   ├── models/
│   │   ├── project.py           # ProjectCreate/ProjectUpdate/Project/ProjectListItem
│   │   └── instruction.py       # InstructionContent/InstructionSave/InstructionSnapshot
│   ├── store/
│   │   ├── project_store.py     # raw sqlite3 ProjectStore (no ORM)
│   │   └── file_store.py        # per-project storage dirs
│   ├── engine/
│   │   ├── project_manager.py   # async ProjectManager + domain exceptions
│   │   └── instruction_engine.py# async InstructionEngine
│   └── api/
│       ├── routes.py            # FastAPI /v1 router
│       └── rest_server.py       # create_app() + uvicorn entry (optional)
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

### REST API (optional, not auto-launched in Phase 0)

```bash
python -m project_service.rest_server   # http://127.0.0.1:11440
```

## RPC methods (UDS, JSON-RPC 2.0, `project.*` namespace)

| Method | Params | Returns |
|---|---|---|
| `project.list` | `{include_archived?, only_starred?}` | `ProjectListItem[]` |
| `project.create` | `ProjectCreate` | `Project` |
| `project.get` | `{project_id}` | `Project` |
| `project.update` | `{project_id, fields: ProjectUpdate}` | `Project` |
| `project.archive` | `{project_id}` | `Project` |
| `project.unarchive` | `{project_id}` | `Project` |
| `project.star` | `{project_id, starred?}` | `Project` |
| `project.delete` | `{project_id}` | `{deleted: true}` (requires archived) |
| `project.instruction.get` | `{project_id}` | `InstructionContent` |
| `project.instruction.save` | `{project_id, content}` | `InstructionContent` |
| `project.instruction.clear` | `{project_id}` | `{cleared: bool}` |
| `project.instruction.snapshots` | `{project_id}` | `InstructionSnapshot[]` |

Framing: newline-delimited JSON. Error codes: `-32700` parse, `-32601` method
not found, `-32602` invalid/missing params, `-32000/1/2` project errors
(generic / not-found / not-archived), `-32603` internal.

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

`GET /projects` · `POST /projects` · `GET|PATCH /projects/{id}` ·
`POST /projects/{id}/archive|unarchive|star` · `DELETE /projects/{id}` (409 if
not archived) · `GET|PUT|DELETE /projects/{id}/instructions` ·
`GET /projects/{id}/instructions/snapshots` · `GET /health`.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `FUSION_PROJECT_SOCK` | `/tmp/fusion-project-svc.sock` | UDS socket path |
| `FUSION_PROJECT_HOST` | `127.0.0.1` | REST host |
| `FUSION_PROJECT_PORT` | `11440` | REST port |
| `FUSION_PROJECT_HOME` | `~/.fusion-projects` | data + storage root |
| `FUSION_MLX_URL` | `http://127.0.0.1:11434/v1` | fusion-mlx base URL |
| `FUSION_RAG_URL` | `http://127.0.0.1:11436` | fusion-rag/kb base URL |
| `FUSION_AGENT_STUDIO_URL` | `http://127.0.0.1:8000` | agent-studio URL |
| `FUSION_GATEWAY_URL` | `http://127.0.0.1:8100` | fusion-gateway URL |
| `FUSION_COWORK_SOCK` | `/tmp/fusion-cowork.sock` | cowork UDS socket |

> Config uses module constants + env overrides (no yaml). This intentionally
> follows the Fusion ecosystem convention and deviates from the config.yaml
> proposal in architecture doc Appendix C.

## Storage layout

```
~/.fusion-projects/
├── data/projects.db                              # SQLite (projects, instructions, instruction_snapshots)
└── storage/{project_id}/{knowledge,attachments,snapshots,exports}/
```

SQLite tables: `projects`, `instructions` (one active row per project, upserted),
`instruction_snapshots` (history; auto-snapshot on instruction change). Dates are
ISO-8601 UTC; IDs are `uuid4().hex`. Foreign keys cascade on project delete.

## Business rules

- **Delete requires archive:** `project.delete` / `DELETE /projects/{id}` returns
  error `-32002` / HTTP `409` unless the project is archived first.
- **Instruction length:** capped at `MAX_INSTRUCTION_CHARS` (10000).
- **Instruction snapshots:** saving a changed instruction auto-snapshots the
  previous content (label `auto`).

## Tests

```bash
source .venv/bin/activate
pytest -q          # 25 tests, no LLM/model loading (Phase 0 has no inference)
```

Coverage: `ProjectStore` CRUD/filters/cascade, `ProjectManager` lifecycle +
archive-before-delete, `InstructionEngine` save/snapshot/clear + length
validation, UDS `ProjectRPCServer` end-to-end via `ProjectClient`, REST API via
`httpx.ASGITransport`. All tests use `tmp_path` - the real `~/.fusion-projects`
is never touched.

## Conventions

- Raw `sqlite3` with `sqlite3.Row` (no ORM), `@contextmanager` cursor with
  commit/rollback + `threading.RLock`.
- UDS JSON-RPC daemon hand-rolled on `asyncio.start_unix_server` (no RPC library)
  - mirrors `fusion-cowork` `DeskRPCServer`.
- `logger = logging.getLogger(__name__)` per module; `logging.basicConfig` only in
  entry points.
- 4-space indentation, no docstrings.
