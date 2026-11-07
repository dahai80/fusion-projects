---
doc_id: fusion-projects/troubleshooting
product: fusion-projects
class: app
entry: service
doc_type: troubleshooting
title: { zh: "故障排查", en: "Troubleshooting" }
audience: engineer
status: draft
version: 0.7.5
updated: 2026-11-07
owner: @dahai
source_of_truth:
  - start.sh
  - README.md
---

# 故障排查 / Troubleshooting

先看进程状态与两类健康端点:`./start.sh status`、`GET /health`(进程存活)、`GET /ready`(上游依赖 fan-out,会指明哪个上游不在线)。

## 启动

**`start.sh start` 直接拒绝启动**

- 端口注册表自检失败:确认 `~/fusion/architecture/port-registry.yaml` 中本服务声明了 11440;`FUSION_PROJECT_PORT` 覆盖为未注册端口会在启动期失败,这是有意设计(配置漂移早失败)。

**REST 401 / 全部请求被拒**

- 未配置 `FUSION_REST_API_KEY` 且未显式 `FUSION_REST_ALLOW_NO_AUTH=1` 时服务拒绝启动;若看到 no-auth 告警说明处于显式关闭认证状态,生产环境必须改回密钥模式。

**UDS 连接失败**

- socket 权限固定 0o600,跨用户调用会失败(设计如此);确认调用方与守护进程同用户,且单行请求不超过 `FUSION_UDS_MAX_LINE_BYTES`。

## 聊天与 RAG

**聊天请求 502/超时**

- 聊天依赖上游链路 project-svc → fusion-rag(11436) → fusion-mlx(11434)(或经 fusion-gateway 11432)。`GET /ready` 会列出哪个上游不在线;先启动对应服务再重试。

**RAG 检索结果为空或分数低**

- 检索链使用 BGE-M3 嵌入,分数阈值 ≥ 0.6;先确认 `POST /projects/{id}/rag/index/{file_id}` 已建索引且 `GET /projects/{id}/knowledge/status` 显示索引完成,再查 `GET /projects/{id}/rag/config`。

**知识文件上传被拒**

- 三类防护会拒绝:文件超过 `FUSION_KNOWLEDGE_MAX_FILE_BYTES`、路径位于 `FUSION_KNOWLEDGE_IMPORT_ROOTS` 之外、目标属于敏感目录;项目/全局配额(`FUSION_KNOWLEDGE_*_QUOTA`)打满也会拒绝,清理或调额后重试。

## 限流与导出

**429 Too Many Requests**:每 IP 限流(`FUSION_REST_RATE_LIMIT` / `FUSION_REST_RATE_WINDOW`)触发;等待窗口或调阈值。限流表容量 `FUSION_RATE_MAX_IPS`。

**导出中断/分块异常**:导出受 `FUSION_EXPORT_MAX_MESSAGES_PER_CHAT`、`FUSION_EXPORT_CHUNK_BYTES`、`FUSION_EXPORT_INLINE_MAX_BYTES` 约束;大项目先看消息数是否触顶。

## 数据与迁移

- SQLite 开启 WAL + busy_timeout,写冲突通常自愈;持续 `database is locked` 检查是否有长时间占用连接的外部读端。
- 迁移回滚走 UDS RPC `migrate.status` / `migrate.down`(v0.4.3 起),不要手工改表。
- 测试基线:`pytest tests/`(集成用例标记 `integration`,需真实上游在线)。

仍未解决:带上 `./start.sh status`、`GET /ready` 响应体与 `logs/` 对应时间段片段提 issue。
