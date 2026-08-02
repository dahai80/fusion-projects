# 架构合规整改计划

> 审计日期: 2026-08-02
> 关联 Issue: #6
> 违规等级: P1（职责边界违规，需近期整改）
> 合规评级: A（P1-S1 + P1-S2 全部整改完成）

## 层级定位

**三、基础核心产品** — 全局项目空间

核心职责：项目资产生命周期管理。

## 违规项与整改

| # | 违规项 | 整改方案 | 目标去向 | 状态 |
|---|--------|----------|----------|------|
| 1 | upstream_client.py CircuitBreaker | 移除，以 GatewayClient 替代 | fusion-gateway | ✅ v0.2.0 |
| 2 | rag_coordinator.py RAG 编排 | GatewayClient 直接调 fusion-kb，无 CircuitBreaker | fusion-kb via GatewayClient | ✅ v0.2.0 |
| 3 | cowork_bridge.py + cowork_tasks 表 | 移除文件和模型，功能由 fusion-cowork 承担 | fusion-cowork | ✅ v0.2.0 |
| 4 | API 路由越界 | 清理：移除 /v1/agents、/v1/upstream、/v1/cowork | - | ✅ v0.1.1 |
| 5 | Message.rag_sources/tool_calls/token_usage | 数据模型清洗：移除推理/协同相关字段 | - | ✅ v0.1.1 |

## P1-S1 整改详情 (v0.2.0)

### 已移除文件
- `engine/upstream_client.py` — CircuitBreaker + UpstreamClient 全部移除
- `engine/cowork_bridge.py` — CoworkBridge + CoworkTaskNotFound 全部移除
- `models/cowork.py` — CoworkTask + CoworkTrigger 模型移除
- `tests/test_upstream_client.py` — CircuitBreaker 单元测试移除（7 项）

### 新增文件
- `engine/gateway_client.py` — 轻量 GatewayClient，无 CircuitBreaker，直接 HTTP 调用

### 已移除配置项
- `config.MLX_BASE_URL` — 不再直接调用 MLX
- `config.MLX_API_KEY` — 不再需要 MLX 认证
- `config.COWORK_SOCK` — 协同功能已迁移

### 修改的依赖注入
- `daemon_server.py`: `upstream: Optional[UpstreamClient]` → `upstream: Optional[GatewayClient]`
- `rag_coordinator.py`: 同上
- `agent_binder.py`: 同上
- `daemon_server.py`: 移除 `self.cowork_bridge = None`

### GatewayClient vs UpstreamClient
| 特性 | UpstreamClient (旧) | GatewayClient (新) |
|------|---------------------|-------------------|
| CircuitBreaker | 4 个 (mlx/rag/agent_studio/gateway) | 无，由 fusion-gateway 承担 |
| MLX 直接调用 | mlx_chat, mlx_models, mlx_is_healthy | 无，经 gateway 调度 |
| Gateway 路由端口 | 8100 | 11432 (gateway 实际端口) |
| cowork 功能 | 无 (P1-S2 已移除路由) | 无 |

## P1-S2 整改详情 (v0.1.1)

### 已移除的 REST 路由
- `GET /v1/agents` — agent 列表属 agent-studio 层
- `GET /v1/agents/{agent_id}` — agent 预览属 agent-studio 层
- `GET /v1/upstream/health` — 上游健康检查属网关层
- `GET /v1/upstream/circuits` — 熔断器状态属网关层
- `POST /v1/cowork/trigger` — 协同触发属 fusion-cowork 层
- `GET /v1/cowork/{task_id}/status` — 协同状态属 fusion-cowork 层

### 已移除的 RPC 方法
- `project.upstream.health`
- `project.upstream.circuits`
- `cowork.trigger`
- `cowork.status`

### 已移除的数据模型字段
- `Message.rag_sources`
- `Message.tool_calls`
- `Message.token_usage`

## 合规标准

整改完成后，API 路由只包含：
- /v1/projects/* — 项目 CRUD + 指令 + 审计 + 导出
- /v1/knowledge/* — 知识库关联 + RAG 索引/检索

不应包含：agents（非项目绑定）、upstream、cowork 路由。
