# 架构合规整改计划

> 审计日期: 2026-08-02
> 关联 Issue: #6
> 违规等级: P1（职责边界违规，需近期整改）
> 合规评级: C

## 层级定位

**三、基础核心产品** — 全局项目空间

核心职责：项目资产生命周期管理。

## 违规项与整改

| # | 违规项 | 整改方案 | 目标去向 | 截止 |
|---|--------|----------|----------|------|
| 1 | upstream_client.py CircuitBreaker | 移除，跨服务通信经 fusion-gateway | fusion-gateway | P1-S1 |
| 2 | rag_coordinator.py RAG 编排 | 移除，RAG 操作经网关调度 | fusion-kb via gateway | P1-S1 |
| 3 | cowork_bridge.py + cowork_tasks 表 | 移至 fusion-cowork | fusion-cowork | P1-S1 |
| 4 | API 路由越界 | 清理：只保留 /v1/projects/* 和 /v1/knowledge/* | - | P1-S2 |
| 5 | Message.rag_sources/tool_calls/token_usage | 数据模型清洗：移除推理/协同相关字段 | - | P1-S2 |

## 合规标准

整改完成后，API 路由只包含：
- /v1/projects/* — 项目 CRUD
- /v1/knowledge/* — 知识库关联

不应包含：agents、rag、upstream、cowork 路由。
