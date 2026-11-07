---
doc_id: fusion-projects/config-reference
product: fusion-projects
class: app
entry: service
doc_type: config-reference
title: { zh: "配置参考", en: "Configuration Reference" }
audience: engineer
status: draft
version: 0.7.5
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - project_service/config.py
  - start.sh
---

# 配置参考 / Configuration Reference

全部配置经环境变量注入,定义在 `project_service/config.py`。未列出的默认值以该文件为准。

## 认证与 REST 防护

| 变量 | 说明 |
|---|---|
| `FUSION_REST_API_KEY` | REST Bearer/x-api-key 密钥;为空时拒绝启动(除非显式允许) |
| `FUSION_REST_ALLOW_NO_AUTH` | `1` 显式关闭认证(启动时大声告警,不静默) |
| `FUSION_REST_MAX_BODY_BYTES` | 请求体大小上限 |
| `FUSION_REST_RATE_LIMIT` / `FUSION_REST_RATE_WINDOW` | 每 IP 限流阈值与窗口 |
| `FUSION_REST_TRUST_PROXY` | 是否信任代理头(限流取客户端 IP 用) |
| `FUSION_RATE_MAX_IPS` | 限流表容量 |

## 上游与连接池

| 变量 | 说明 |
|---|---|
| `FUSION_CLIENT_KEY` | 全 fusion 栈规范凭据(网关/引擎通用;`FUSION_GATEWAY_API_KEY` 为废弃别名) |
| `FUSION_AGENT_STUDIO_URL` | Agent 绑定上游,默认 `http://127.0.0.1:11442`(start.sh 注入) |
| `FUSION_GATEWAY_POOL_MAX_CONN` / `FUSION_GATEWAY_POOL_MAX_KEEPALIVE` | 上游连接池 |

## 知识库与 RAG

| 变量 | 说明 |
|---|---|
| `FUSION_KNOWLEDGE_MAX_FILE_BYTES` | 单文件大小上限 |
| `FUSION_KNOWLEDGE_IMPORT_ROOTS` / `FUSION_TEMP_IMPORT_ROOT` | 允许导入的根目录(路径穿越防护;敏感目录拒绝) |
| `FUSION_ALWAYS_INCLUDE_MAX_BYTES` | always-include 注入总量上限 |
| `FUSION_KNOWLEDGE_PROJECT_QUOTA` / `FUSION_KNOWLEDGE_GLOBAL_QUOTA` | 项目/全局存储配额 |
| `FUSION_RAG_INDEX_CONCURRENCY` | 索引并发 |
| `FUSION_RAG_MAX_TOP_K` / `FUSION_RAG_MAX_FOLDER_SCOPE` | 检索与范围上限 |

## 存储、导出与运行

| 变量 | 说明 |
|---|---|
| `FUSION_PROJECT_PORT` | REST 端口,默认 11440(start.sh 强制端口注册表校验) |
| `FUSION_UDS_MAX_LINE_BYTES` / `FUSION_MCP_MAX_LINE_BYTES` | UDS/MCP 单行上限 |
| `FUSION_WAL_AUTO_CHECKPOINT` | SQLite WAL checkpoint 策略 |
| `FUSION_EXPORT_INLINE_MAX_BYTES` / `FUSION_EXPORT_MAX_MESSAGES_PER_CHAT` / `FUSION_EXPORT_CHUNK_BYTES` | 导出分块参数 |
| `FUSION_FORK_MAX_MESSAGES` / `FUSION_SNAPSHOT_MAX_MESSAGES` | fork/快照消息数上限 |
| `FUSION_LOG_MAX_BYTES` / `FUSION_LOG_BACKUP_COUNT` | 日志轮转 |

约束:密钥支持从文件加载(secret-file),避免出现在进程环境回显中;改任何限流/配额值后用 `GET /ready` 与一次真实请求验证,不做过期假设。
