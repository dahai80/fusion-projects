---
doc_id: fusion-projects/api-reference
product: fusion-projects
class: app
entry: service
doc_type: api-reference
title: { zh: "API 参考", en: "API Reference" }
audience: engineer
status: draft
version: 0.7.5
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - project_service/api
  - project_service/daemon_server.py
  - project_service/mcp_server.py
---

# API 参考 / API Reference

三个入口共享同一套领域模型:UDS JSON-RPC(76 个方法,含发现方法)、REST(端口 11440)、MCP(9 个工具)。REST 需要 Bearer(`Authorization: Bearer <key>`)或 `x-api-key` 头;`FUSION_REST_API_KEY` 为空且未显式 `FUSION_REST_ALLOW_NO_AUTH=1` 时拒绝启动。错误响应统一 `{ detail: string }` + HTTP 状态码。

## REST 路由(共 65 条,以下按资源分组)

### 项目

| 方法与路径 | 说明 |
|---|---|
| `GET /projects` | 项目列表(`list[ProjectListItem]`) |
| `POST /projects` | 建项目,201 |
| `GET /projects/{project_id}` | 项目详情 |
| `PATCH /projects/{project_id}` | 部分更新 |
| `DELETE /projects/{project_id}` | 删除,204 |
| `POST /projects/{project_id}/duplicate` | 复制 |
| `POST /projects/{project_id}/archive` / `unarchive` | 归档/恢复 |
| `POST /projects/{project_id}/star` | 星标 |
| `POST /projects/{project_id}/export` / `artifacts/export` | 全量导出(分块,受 `FUSION_EXPORT_*` 约束) |

### 指令与 Agent 绑定

| 方法与路径 | 说明 |
|---|---|
| `GET/PUT/DELETE /projects/{id}/instructions` | 全局指令内容读写删 |
| `GET/PUT/DELETE /projects/{id}/agent` | Agent 绑定(`AgentBinding`) |

### 知识库与 RAG

| 方法与路径 | 说明 |
|---|---|
| `GET/POST /projects/{id}/knowledge/folders` · `PATCH/DELETE .../folders/{folder_id}` | 文件夹管理 |
| `GET/POST /projects/{id}/knowledge/files` · `GET/PATCH/DELETE .../files/{file_id}` | 文件管理 |
| `POST /projects/{id}/knowledge/files/upload` | 上传(路径穿越 + 大小防护) |
| `POST /projects/{id}/knowledge/files/{file_id}/replace` / `always-include` | 替换 / 始终注入 |
| `GET /projects/{id}/knowledge/status` | 每文件索引状态 |
| `POST /projects/{id}/rag/index/{file_id}` / `rag/index-folder/{folder_id}` | 建索引 |
| `GET /projects/{id}/rag/config` / `rag/status` · `PUT /projects/{id}/rag/config` | RAG 配置与状态 |
| `POST /projects/{id}/rag/query` | 检索(BGE-M3,分数阈值 ≥ 0.6) |

### 聊天

| 方法与路径 | 说明 |
|---|---|
| `GET/POST /projects/{id}/chats` · `GET/PATCH/DELETE /projects/{id}/chats/{chat_id}` | 会话 CRUD |
| `GET/POST /projects/{id}/chats/{chat_id}/messages` | 消息列表/追加 |
| `POST /projects/{id}/chats/{chat_id}/messages/stream` | SSE 流式聊天(自动注入指令 + RAG) |
| `POST /projects/{id}/chats/{chat_id}/fork` / `move` / `detach` / `star` | fork / 移动 / 解绑 / 星标 |
| `GET/POST /projects/{id}/chats/{chat_id}/snapshots` | 快照列表/创建 |
| `POST /projects/{id}/chats/{chat_id}/snapshots/{snapshot_id}/restore` | 恢复快照 |
| `DELETE /projects/{id}/chats/{chat_id}/messages/{message_id}` · `.../snapshots/{snapshot_id}` | 删除消息/快照 |

### 其他

| 方法与路径 | 说明 |
|---|---|
| `GET/POST /projects/{id}/artifacts` · `DELETE .../artifacts/{artifact_id}` | 产物引用 |
| `GET/POST /projects/{id}/audit` | 审计日志查询/写入 |
| `POST /projects/{id}/system-prompt` | 系统提示组装 |
| `GET /health` · `GET /ready` · `GET /metrics` | 存活 / 依赖 fan-out / Prometheus 指标 |
| `POST /mcp` | MCP 协议端点 |

## UDS JSON-RPC

socket 路径由守护进程管理,权限 0o600。方法命名与 REST 能力一一对应(如 `project.chat.message.stream` 对应 SSE 路由),另有发现方法 `ping`、`rpc.list`、`tools.list`、迁移方法 `migrate.status` / `migrate.down`。完整方法清单运行期可查:`rpc.list`。

## MCP 工具

9 个工具(如 `project_send_message`),经 `POST /mcp` 或 UDS 暴露,清单运行期 `tools/list` 可查。

限制:路由与 RPC 数量以代码为准(`project_service/api/`),本文件只列分组与代表性路径;新增路由须同步本表。
