# fusion-project-svc

[English](README.md) | **中文**

Fusion 生态的本地优先 AI **项目资产容器**服务。*项目（Project）* 是一个隔离的工作域，捆绑全局指令、持久化知识库（RAG）、隔离的聊天会话以及绑定的 Fusion Agent。本服务负责项目元数据、指令和存储布局，对外提供 UDS JSON-RPC 守护进程（供 Fusion 桌面端/Agent 调用）以及可选的 REST API。

> **状态：v0.3.1 — 公网/互联网部署的生产加固版。**
> 完整的项目 CRUD、指令 + 快照、知识库文件夹/文件、聊天会话 + 分支 + 移动 + 解绑、Agent 绑定、RAG 索引 + 检索、审计日志、MCP 服务及完整项目导出均已实现且全量通过。CircuitBreaker + 协同桥接已移除，UpstreamClient 替换为 GatewayClient（P1-S1 合规）。RAG 链路端到端验证通过：project-svc → fusion-rag → fusion-mlx，使用 BGE-M3 嵌入（score ≥ 0.6）。
> 核心特性验收：75 个 RPC 方法（含 ping/rpc.list/tools/list 发现）、9 个 MCP 工具、65 条 REST 路由含 SSE 流式、13 张 SQLite 表带外键级联、107 个测试全通过。
> **生产加固（v0.3.0）：** REST Bearer/x-api-key 鉴权（公共路径豁免）、按 IP 限流、请求体大小上限、SQLite WAL + busy_timeout、UDS socket 0o600、SIGTERM 优雅关闭并清理客户端连接、密钥文件加载、知识库上传路径遍历 + 体积防护。
> **v0.3.1 补丁：** AGENT_STUDIO_URL 默认 8000→11455，对齐 fusion 114xx 端口约定（修复 Agent 绑定连通性，#22）；抖音电商 AgentGraph 接入商品主图抓取 + CDP 真实发布（#23）。

## 目录结构

```
fusion-projects/
├── pyproject.toml
├── start.sh                     # UDS 守护进程 start|stop|restart|status
├── project_service/
│   ├── __init__.py
│   ├── config.py                # 路径、端口、URL、默认值（可通过环境变量覆盖）
│   ├── client.py                # ProjectClient - UDS JSON-RPC 客户端 + RPCError
│   ├── daemon_server.py         # ProjectRPCServer - UDS JSON-RPC 2.0 守护进程
│   ├── mcp_server.py            # MCP JSON-RPC 服务（Claude/Cursor 集成）
│   ├── models/
│   │   ├── project.py           # ProjectCreate/ProjectUpdate/Project/ProjectListItem
│   │   ├── instruction.py       # InstructionContent/InstructionSave/InstructionSnapshot
│   │   ├── chat.py              # Chat/ChatCreate/ChatUpdate/Message/TempAttachment
│   │   ├── knowledge.py         # KnowledgeFolder/KnowledgeFile/FolderCreate/FolderUpdate
│   │   ├── agent_binding.py     # AgentBinding/AgentMeta/AgentPreview/PromptMergeMode
│   │   ├── artifact_ref.py      # ArtifactRef/ArtifactMigrateRequest
│   │   ├── audit.py             # AuditLogEntry
│   │   └── cowork.py            # 已移除（P1-S1）
│   ├── store/
│   │   ├── project_store.py     # 原生 sqlite3 ProjectStore（15 张表，无 ORM）
│   │   └── file_store.py        # 按项目隔离的存储目录
│   ├── engine/
│   │   ├── project_manager.py   # 异步 ProjectManager + 领域异常
│   │   ├── instruction_engine.py# 异步 InstructionEngine + 快照恢复/删除
│   │   ├── chat_manager.py      # 异步 ChatManager + 移动/解绑/临时附件
│   │   ├── knowledge_manager.py # 异步 KnowledgeManager + 文件上传/替换
│   │   ├── agent_binder.py      # 异步 AgentBinder + 上游 Agent 解析
│   │   ├── rag_coordinator.py   # 异步 RAGCoordinator（委托 fusion-rag）
│   │   └── gateway_client.py    # 异步 GatewayClient（无熔断器，委托网关路由）
│   └── api/
│       ├── routes.py            # FastAPI /v1 路由 + MCP 端点
│       └── rest_server.py       # create_app() + uvicorn 入口
└── tests/                       # pytest，asyncio_mode=auto
```

## 安装

```bash
cd ~/fusion/fusion-projects
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

## 运行

### UDS 守护进程（主入口 — 由 `start.sh` 启动）

```bash
./start.sh start    # 监听 /tmp/fusion-project-svc.sock (chmod 0o666)
./start.sh status
./start.sh stop
./start.sh restart
```

日志：`logs/stdout.log`、`logs/stderr.log`。PID：`.fusion-project-svc.pid`。

### REST API（可选）

```bash
python -m project_service.rest_server   # http://127.0.0.1:11440
```

### MCP 服务（stdio 模式，供 Claude/Cursor 使用）

```bash
python -m project_service.mcp_server    # 通过 stdin/stdout 通信
```

## RPC 方法（UDS，JSON-RPC 2.0）

### 项目

| 方法 | 参数 | 返回 |
|---|---|---|
| `project.list` | `{include_archived?, only_starred?}` | `ProjectListItem[]` |
| `project.create` | `ProjectCreate`（name, description?, instructions?, template_id?） | `Project` |
| `project.get` | `{project_id}` | `Project` |
| `project.update` | `{project_id, fields: ProjectUpdate}` | `Project` |
| `project.archive` | `{project_id}` | `Project` |
| `project.unarchive` | `{project_id}` | `Project` |
| `project.star` | `{project_id, starred?}` | `Project` |
| `project.delete` | `{project_id}` | `{deleted: true}`（需先归档） |
| `project.duplicate` | `{project_id, name?}` | `Project` |
| `project.export` | `{project_id}` | `{zip_base64, size}` |
| `project.artifact.list` | `{project_id, artifact_type?, limit?, offset?}` | `ArtifactRef[]` |
| `project.artifact.migrate` | `ArtifactMigrateRequest` | `ArtifactRef` |
| `project.artifact.export` | `{project_id, artifact_ids?}` | zip 字节流 |

### 指令

| 方法 | 参数 | 返回 |
|---|---|---|
| `project.instruction.get` | `{project_id}` | `InstructionContent` |
| `project.instruction.save` | `{project_id, content}` | `InstructionContent` |
| `project.instruction.clear` | `{project_id}` | `{cleared: bool}` |
| `project.instruction.snapshots` | `{project_id}` | `InstructionSnapshot[]` |
| `project.instruction.snapshot.restore` | `{snapshot_id}` | `InstructionContent` |
| `project.instruction.snapshot.delete` | `{snapshot_id}` | `{deleted: true}` |

### 聊天

| 方法 | 参数 | 返回 |
|---|---|---|
| `project.chat.list` | `{project_id, only_starred?}` | `ChatListItem[]` |
| `project.chat.create` | `{project_id, title?}` | `Chat` |
| `project.chat.get` | `{chat_id}` | `Chat` |
| `project.chat.update` | `{chat_id, fields}` | `Chat` |
| `project.chat.delete` | `{chat_id}` | `{deleted: true}` |
| `project.chat.star` | `{chat_id, starred?}` | `Chat` |
| `project.chat.fork` | `{chat_id, label?}` | `Chat` |
| `project.chat.move` | `{chat_id, target_project_id}` | `Chat` |
| `project.chat.detach` | `{chat_id}` | `Chat`（project_id=null） |
| `project.chat.message.add` | `{chat_id, content, role?}` | `Message` |
| `project.chat.message.list` | `{chat_id, limit?, offset?}` | `{messages, total}` |
| `project.chat.temp_attachment.add` | `{chat_id, file_path, original_name, file_size, mime_type?}` | `TempAttachment` |
| `project.chat.temp_attachment.list` | `{chat_id}` | `TempAttachment[]` |
| `project.chat.temp_attachment.delete` | `{attachment_id}` | `{deleted: true}` |

### 知识库

| 方法 | 参数 | 返回 |
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

### Agent 绑定

| 方法 | 参数 | 返回 |
|---|---|---|
| `project.agent.set` | `{project_id, agent_id, merge_mode?}` | `AgentBinding` |
| `project.agent.get` | `{project_id, chat_id?}` | `AgentBinding` |
| `project.agent.preview` | `{project_id}` | `AgentPreview` |
| `project.agent.remove` | `{binding_id}` | `{deleted: true}` |

### RAG

| 方法 | 参数 | 返回 |
|---|---|---|
| `project.rag.index` | `{project_id, folder_id}` | `{indexed, results}` |
| `project.rag.query` | `{project_id, query, mode?, folder_ids?, top_k?, threshold?, chat_id?}` | 检索结果 |
| `project.rag.index.remove` | `{file_id}` | `{removed: true}` |
| `project.rag.status` | `{project_id}` | 状态 |
| `project.rag.config.get` | `{project_id}` | `{rag_mode, rag_top_k, rag_threshold}` |
| `project.rag.config.set` | `{project_id, rag_mode?, rag_top_k?, rag_threshold?}` | 配置 |

### 审计

| 方法 | 参数 | 返回 |
|---|---|---|
| `project.audit.list` | `{project_id, limit?, offset?}` | `AuditLogEntry[]` |
| `project.audit.log` | `{project_id, action, chat_id?, agent_id?, details?}` | `AuditLogEntry` |

错误码：`-32700` 解析错误，`-32601` 方法未找到，`-32602` 参数无效/缺失，`-32000` 项目通用错误，`-32001` 项目未找到，`-32002` 未归档，`-32005` 聊天未找到，`-32006` 文件夹未找到，`-32007` 知识文件未找到，`-32008` Agent 绑定错误，`-32009` RAG 错误，`-32010` 快照未找到，`-32603` 内部错误。

### 客户端（Python）

```python
import asyncio
from project_service.client import ProjectClient

async def main():
    c = ProjectClient()                       # 默认 /tmp/fusion-project-svc.sock
    p = await c.create_project(name="My Project", description="...")
    await c.save_instruction(p["id"], "Be concise and cite sources.")
    print(await c.list_projects())

asyncio.run(main())
```

## REST 端点（`/v1`）

项目：`GET /projects` · `POST /projects` · `GET|PATCH /projects/{id}` ·
`POST /projects/{id}/archive|unarchive|star|duplicate|export` ·
`DELETE /projects/{id}`

指令：`GET|PUT|DELETE /projects/{id}/instructions` ·
`GET /projects/{id}/instructions/snapshots` ·
`POST /projects/{id}/instructions/snapshots/{sid}/restore|delete`

聊天：`GET /projects/{id}/chats` · `POST /projects/{id}/chats` ·
`GET|PATCH|DELETE /chats/{id}` · `POST /chats/{id}/star|fork|move|detach` ·
`GET /chats/{id}/messages` · `POST /chats/{id}/messages` ·
`POST /chats/{id}/messages/stream`（SSE）·
`GET|POST|DELETE /chats/{id}/temp-attachments`

知识库：`GET /projects/{id}/knowledge/folders` ·
`POST|PATCH|DELETE /knowledge/folders/{id}` ·
`GET /projects/{id}/knowledge/files` ·
`POST /projects/{id}/knowledge/files/upload|replace` ·
`PATCH|DELETE /knowledge/files/{id}`

Agent：`POST /projects/{id}/agent` · `GET /projects/{id}/agent` ·
`DELETE /projects/{id}/agent` · `POST /projects/{id}/system-prompt`

RAG：`POST /projects/{id}/rag/index|query` ·
`DELETE /projects/{id}/rag/index/{file_id}` ·
`GET /projects/{id}/rag/status|config` · `PUT /projects/{id}/rag/config`

制品：`GET /projects/{id}/artifacts` ·
`POST /projects/{id}/artifacts/migrate|export`

审计：`GET|POST /projects/{id}/audit`

MCP：`POST /mcp`（JSON-RPC 2.0，支持 tools/list、tools/call、initialize）

## MCP 工具（供 Claude/Cursor 使用）

| 工具 | 说明 |
|---|---|
| `project_list` | 列出所有项目 |
| `project_get` | 获取项目详情 |
| `project_search_knowledge` | 通过 RAG 搜索项目知识库 |
| `project_list_knowledge` | 列出项目知识文件 |
| `project_get_instructions` | 获取项目指令 |
| `project_list_chats` | 列出项目聊天 |
| `project_get_chat_messages` | 获取聊天消息 |

## 配置（环境变量）

| 变量 | 默认值 | 用途 |
|---|---|---|
| `FUSION_PROJECT_SOCK` | `/tmp/fusion-project-svc.sock` | UDS 套接字路径 |
| `FUSION_PROJECT_HOST` | `127.0.0.1` | REST 主机 |
| `FUSION_PROJECT_PORT` | `11440` | REST 端口 |
| `FUSION_PROJECT_HOME` | `~/.fusion-projects` | 数据 + 存储根目录 |
| `FUSION_RAG_URL` | `http://127.0.0.1:11436` | fusion-rag/kb 基础 URL |
| `FUSION_RAG_EMBEDDING_MODEL` | `BAAI--bge-m3` | 知识库创建时使用的嵌入模型 ID |
| `FUSION_AGENT_STUDIO_URL` | `http://127.0.0.1:8000` | agent-studio URL |
| `FUSION_GATEWAY_URL` | `http://127.0.0.1:11432` | fusion-gateway URL |

## RAG 端到端链路

项目服务集成了 fusion-rag（端口 11436）和 fusion-mlx（端口 11434）用于知识库索引和检索。已验证的流程：

```
project-svc (UDS RPC)
  → rag_coordinator.index_file() / .query()
    → gateway_client.rag_upload_doc() / .rag_search()
      → fusion-rag /kb/bases/{kb_id}/documents（上传 + 嵌入）
        → fusion-mlx /api/v1/embeddings（BAAI--bge-m3，1024 维）
      → fusion-rag /kb/bases/{kb_id}/search（向量相似度）
```

**前置条件：**
- fusion-mlx 在 11434 端口运行，已加载 BGE-M3 模型
- fusion-rag 在 11436 端口运行，配置了 `mlx_api_key` 和 `embedding_model='BAAI--bge-m3'`
- project-svc 启动时设置 `FUSION_MLX_API_KEY=dahai168 FUSION_RAG_EMBEDDING_MODEL=BAAI--bge-m3`

**已验证的端到端流程：**
1. `project.create` → 创建项目，`kb_id: null`
2. `project.knowledge.folder.create` → 创建知识库文件夹
3. `project.knowledge.file.upload` → 复制文件到项目存储，`index_status: PENDING`
4. `project.rag.index_file` → 调用 fusion-rag 上传 → 首次使用时创建 KB → 存储 `kb_id` → 返回 `doc_id, chunks, chars`
5. `project.rag.query` → 调用 fusion-rag 搜索 → 返回 `score ≥ 0.6` 的结果

## 存储布局

```
~/.fusion-projects/
├── data/projects.db                              # SQLite（15 张表）
└── storage/{project_id}/{knowledge,attachments,snapshots,exports}/
```

SQLite 表：`projects`、`instructions`、`instruction_snapshots`、
`project_artifacts`、`chats`、`chat_snapshots`、`messages`、
`knowledge_folders`、`knowledge_files`、`chat_agent_bindings`、`rag_queries`、
`temp_attachments`、`audit_log`。日期为 ISO-8601 UTC；ID
为 `uuid4().hex`。删除项目时外键级联。

## 业务规则

- **删除需先归档：** `project.delete` / `DELETE /projects/{id}` 在项目
  未归档时返回错误 `-32002` / HTTP `409`。
- **指令长度：** 上限为 `MAX_INSTRUCTION_CHARS`（10000）。
- **指令快照：** 保存变更的指令时自动快照之前的内容（标签 `auto`）。快照可恢复或删除。
- **聊天 project_id 可空：** 聊天可从项目解绑（project_id=NULL）。
- **完整项目导出：** 创建包含 project.json、instructions.json、
  聊天 + 消息、知识库文件夹/文件、Agent 绑定的 ZIP。

## 测试

```bash
source .venv/bin/activate
pytest -q          # 94 个测试，无需 LLM/模型加载
```

覆盖范围：`ProjectStore` CRUD/过滤/级联，`ProjectManager` 生命周期 +
归档后删除 + 复制 + 导出，`InstructionEngine` 保存/快照/
恢复/删除 + 长度校验，`ChatManager` 移动/解绑/临时附件，
`KnowledgeManager` 文件夹/文件 CRUD + 上传/替换，`AgentBinder` 设置/
获取/预览，`RAGCoordinator` 配置，`GatewayClient` RAG/Agent 委托，`MCPServer`
initialize/tools-list/解析错误，UDS `ProjectRPCServer` 通过
`ProjectClient` 端到端测试，REST API 通过 `httpx.ASGITransport` 测试。
所有测试使用 `tmp_path` — 不会影响真实的 `~/.fusion-projects`。

## 约定

- 原生 `sqlite3` 配合 `sqlite3.Row`（无 ORM），`@contextmanager` 游标
  带 commit/rollback + `threading.RLock`。
- UDS JSON-RPC 守护进程基于 `asyncio.start_unix_server` 手写（无 RPC 库）。
- MCP 服务遵循 2024-11-05 协议规范（initialize → tools/list →
  tools/call）。可通过 `POST /api/v1/mcp`（HTTP）或 stdio（CLI）使用。
- 每个模块使用 `logger = logging.getLogger(__name__)`；`logging.basicConfig`
  仅在入口点调用。
- 4 空格缩进，无 docstring。
