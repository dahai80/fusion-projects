---
doc_id: fusion-projects/get-started
product: fusion-projects
class: app
entry: service
doc_type: get-started
title: { zh: "快速开始", en: "Get Started" }
audience: engineer
status: draft
version: 0.7.5
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - README.md
---

# 快速开始 / Get Started

前提:Python >= 3.12,仓库共享 `.venv`(`cd ~/fusion && source .venv/bin/activate`)。上游可选:fusion-gateway(11432)/ fusion-mlx(11434)/ fusion-rag(11436)——不在线时聊天与 RAG 不可用,项目管理仍可离线运行。

## 1. 安装并启动

```bash
git clone https://github.com/dahai80/fusion-projects.git
cd fusion-projects
pip install -e ".[test]"
./start.sh start          # REST on http://127.0.0.1:11440, UDS daemon alongside
./start.sh status
```

`start.sh` 会在启动前校验端口注册表(架构仓 `architecture/port-registry.yaml`),端口漂移直接拒绝启动而不是运行期失败。

## 2. 认证(生产必须)

```bash
export FUSION_REST_API_KEY="$(openssl rand -hex 32)"
# 或开发环境显式关闭(会打印醒目告警,不是静默放行)
export FUSION_REST_ALLOW_NO_AUTH=1
```

## 3. 走通第一个项目

```bash
# 建项目
curl -s -X POST http://127.0.0.1:11440/projects \
  -H "Authorization: Bearer $FUSION_REST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "demo", "description": "first project"}'
```

然后(同上认证头):

1. `PUT /projects/{id}/instructions` 写入全局指令——聊天时会自动注入。
2. `POST /projects/{id}/knowledge/files/upload` 上传知识文件,`POST /projects/{id}/rag/index/{file_id}` 建索引。
3. `POST /projects/{id}/chats` 建会话,`POST /projects/{id}/chats/{cid}/messages/stream` 发消息(SSE 流式,返回内容已注入指令 + RAG 上下文)。
4. `POST /projects/{id}/chats/{cid}/snapshots` 做快照,`POST .../snapshots/{sid}/restore` 回滚。

健康检查:`GET /health`(进程存活)、`GET /ready`(上游依赖 fan-out)。

## 4. 客户端接入方式(三选一)

| 方式 | 场景 | 入口 |
|---|---|---|
| UDS JSON-RPC | fusion 桌面端 / 同机 agent | `project_service/daemon_server.py`,76 个方法 |
| REST API | Web/脚本/跨进程 | 端口 11440,[api-reference.md](api-reference.md) |
| MCP | 任何 MCP 客户端 | `POST /mcp` + 9 个工具,`project_service/mcp_server.py` |

停止:`./start.sh stop`。下一步:[api-reference.md](api-reference.md) 与 [troubleshooting.md](troubleshooting.md)。
