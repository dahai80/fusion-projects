# 架构合规整改计划

> 审计日期: 2026-08-02
> 关联 Issue: #6
> 违规等级: P1（职责边界违规，需近期整改）
> 合规评级: B（P1-S2 已整改，P1-S1 待上游就绪）

## 层级定位

**三、基础核心产品** — 全局项目空间

核心职责：项目资产生命周期管理。

## 违规项与整改

| # | 违规项 | 整改方案 | 目标去向 | 截止 | 状态 |
|---|--------|----------|----------|------|------|
| 1 | upstream_client.py CircuitBreaker | 移除，跨服务通信经 fusion-gateway | fusion-gateway | P1-S1 | ⏳ 待 fusion-gateway 就绪 |
| 2 | rag_coordinator.py RAG 编排 | 移除，RAG 操作经网关调度 | fusion-kb via gateway | P1-S1 | ⏳ 待 fusion-gateway 就绪 |
| 3 | cowork_bridge.py + cowork_tasks 表 | 移至 fusion-cowork | fusion-cowork | P1-S1 | ⏳ 待 fusion-cowork 就绪 |
| 4 | API 路由越界 | 清理：移除 /v1/agents、/v1/upstream、/v1/cowork | - | P1-S2 | ✅ v0.1.1 |
| 5 | Message.rag_sources/tool_calls/token_usage | 数据模型清洗：移除推理/协同相关字段 | - | P1-S2 | ✅ v0.1.1 |

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

P1-S1 整改完成后，API 路由只包含：
- /v1/projects/* — 项目 CRUD + 指令 + 审计 + 导出
- /v1/knowledge/* — 知识库关联 + RAG 索引/检索

不应包含：agents（非项目绑定）、upstream、cowork 路由。
