---
doc_id: fusion-projects/product-page
product: fusion-projects
class: app
entry: service
doc_type: product-page
title: { zh: "产品页", en: "Product Page" }
audience: engineer
status: draft
version: 0.7.5
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - README.md
---

# fusion-projects

本地优先的 AI 项目资产容器服务。一个 *Project* 是一个隔离的工作域:全局指令、持久知识库(RAG)、隔离的聊天会话、绑定的 Fusion Agent。上游调用方(fusion-studio、agent、MCP 客户端)不再需要各自实现项目元数据、指令注入和知识检索——它们直接订阅本服务。

## 它解决什么问题

| 上游过去要自己做的 | fusion-projects 接管后 |
|---|---|
| 每个应用各自存储项目配置与指令 | 项目 CRUD + 指令/快照统一存储(SQLite,13 张表,FK 级联) |
| 聊天时手工拼装系统提示与知识 | 聊天时自动注入指令 + RAG 知识(v0.3.2 E2E 组装,REST `messages/stream` 与 UDS RPC 同源) |
| 为每个项目重复搭建向量检索 | 知识库文件夹/文件上传、配额、索引状态、RAG 查询一条链路:project-svc → fusion-rag → fusion-mlx(BGE-M3,分数阈值 ≥ 0.6) |
| 自行管理会话历史与回滚 | 聊天 fork/move/detach、快照创建与 restore、导出 |

## 核心能力与边界

- **UDS JSON-RPC 守护进程**(主入口):76 个 RPC 方法(含 `ping`/`rpc.list`/`tools.list` 发现),socket 权限 0o600,供 fusion 桌面端/agent 调用。约束:仅本机 Unix Domain Socket,不监听 TCP。
- **REST API**(可选,端口 11440):65 条路由,含 SSE 流式聊天。约束:需 Bearer/x-api-key 认证(`FUSION_REST_API_KEY`),公开路径白名单之外全部拦截;无认证启动必须显式 `FUSION_REST_ALLOW_NO_AUTH=1` 且会大声告警。
- **MCP server**(`POST /mcp` 与 UDS):9 个工具,标准 MCP 客户端可直接接入。
- **审计日志**:`GET /projects/{id}/audit` 记录操作轨迹。
- **硬ening(v0.3.0)**:每 IP 限流、请求体大小上限、SQLite WAL + busy_timeout、知识上传路径穿越与大小防护、密钥从文件加载。
- 约束(通用):单机本地优先,不做多租户网络服务;fusion-core 缺席时优雅降级为单租户模式(`.[tenant]` extra)。

## 入口

- 启动:`./start.sh start`(REST 端口 11440;UDS 由守护进程管理)。
- 客户端 SDK:`project_service/client.py`(`ProjectClient`)。
- 文档:[get-started.md](get-started.md) · [api-reference.md](api-reference.md) · [config-reference.md](config-reference.md) · [troubleshooting.md](troubleshooting.md)。

## 适用与不适用

- 适用:需要项目级指令/知识/会话隔离的 fusion 生态应用(桌面端、agent、MCP 客户端)。
- 不适用:公网多租户 SaaS;脱离 fusion-mlx/fusion-rag 的独立向量检索。
