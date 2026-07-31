# fusion-projects 全阶段落地计划

## 当前状态 (86 tests passing)

**已完成 (Phase 0 + Phase 1 引擎层):**
- ✅ 11 DB tables (projects, instructions, instruction_snapshots, project_artifacts, chats, chat_snapshots, messages, knowledge_folders, knowledge_files, chat_agent_bindings, rag_queries)
- ✅ Engine: project_manager, instruction_engine, chat_manager, knowledge_manager, agent_binder, rag_coordinator, upstream_client
- ✅ Models: project, instruction, chat, knowledge, agent_binding, artifact_ref
- ✅ REST: 50 endpoints under /api/v1 (project CRUD, instructions, artifacts, chats, messages, knowledge, agent, rag, upstream)
- ✅ RPC: 50+ handlers in daemon_server.py
- ✅ Tests: 86 passing

## 按阶段梳理 — 哪些不依赖上游可落地

### Phase 1 补全 (本地可落地)

**P1-1: instruction_engine 增强 — 指令快照手动 restore/delete**
- 当前: `list_snapshots` ✅, `save` (auto-snapshot) ✅
- 缺失: `restore_snapshot(snapshot_id)`, `delete_snapshot(snapshot_id)`
- 需新增: project_store 方法 + instruction_engine 方法 + RPC/REST handlers
- 无上游依赖

**P1-2: build_system_prompt 实现 (instruction_engine)**
- 当前: agent_binder.build_system_prompt 已实现
- 缺失: instruction_engine 自身无独立 build_system_prompt（架构文档要求）
- 方案: instruction_engine 添加 build_base_prompt(project_id) → 组装项目指令基础 prompt
- 无上游依赖

**P1-3: temp_attachments 表 + 临时附件管理**
- 当前: 无 temp_attachments 表，无附件 CRUD
- 缺失: temp_attachments 表、KnowledgeManager 中的临时附件方法、REST endpoint
- 无上游依赖

**P1-4: chat_manager 会话迁移 move_chat / detach_chat**
- 当前: chat CRUD ✅, fork ✅, snapshot ✅
- 缺失: move_chat(chat_id, target_project_id), detach_chat(chat_id)
- 无上游依赖

**P1-5: knowledge_manager 文件上传/替换/预览**
- 当前: 文件夹 CRUD ✅, 文件 create/list/delete ✅
- 缺失: upload_file (实际文件落盘)、replace_file (保留索引)、preview_file
- 部分依赖: upload 需 file_store 写入磁盘 ✅ 本地可做, replace 需 fusion-rag 文档替换 API (缺)→降级为 delete+re-upload

**P1-6: ProjectCreate 扩展字段**
- 当前: ProjectCreate 仅有 name, description
- 缺失: instructions (创建时填写), template_id, default_agent_id, prompt_merge_mode, rag_config
- 需修改: models/project.py + project_manager.create + project_store
- 无上游依赖

**P1-7: 项目复制 duplicate_project**
- 缺失: duplicate 两种模式 (仅资产 / 资产+会话快照)
- 无上游依赖

**P1-8: audit_log 表 + 审计日志**
- 当前: 无 audit_log 表
- 缺失: audit_log 表 + store 方法 + 关键操作写入审计
- 无上游依赖

### Phase 2 本地可落地部分

**P2-1: SSE 流式推理 endpoint (routes.py)**
- 缺失: POST /projects/{id}/chats/{chatId}/messages 的 SSE 流式响应
- 本地可做: endpoint 骨架 + 上下文组装 + SSE 格式
- 上游依赖: 实际推理需 gateway/MLX 可用 → 先用 mock 验证骨架

**P2-2: RAG 配置持久化 (per-project rag_config)**
- 当前: Project 模型有 rag_mode/rag_top_k/rag_threshold 字段 ✅
- 缺失: get_rag_config / set_rag_config 独立 REST endpoint
- 无上游依赖

### Phase 3 本地可落地部分

**P3-1: cowork_bridge.py 骨架**
- 缺失: 同步/导入/导出的接口定义 + 本地逻辑
- 上游依赖: 实际调用 cowork UDS → 先写骨架 + 降级处理

**P3-2: mcp_server.py**
- 缺失: MCP JSON-RPC server (7 tools)
- 无上游依赖 (纯本地协议转发)

**P3-3: 项目导出增强 (完整 zip)**
- 当前: export_artifacts ✅ (仅 artifact)
- 缺失: 全项目导出 (指令 + 知识库文件 + 会话快照)
- 无上游依赖

**P3-4: 项目删除完善 (归档前置 + 导出备份入口)**
- 当前: delete 需 archived ✅
- 缺失: 删除前提示导出 (REST 层逻辑)
- 无上游依赖

## 执行优先级排序

按价值/复杂度比排序，优先落地无依赖高价值项:

1. **P1-1** instruction_engine 快照 restore/delete — 低复杂度，补齐核心能力
2. **P1-6** ProjectCreate 扩展字段 — 低复杂度，创建项目体验核心
3. **P1-4** chat move/detach — 中复杂度，会话管理核心
4. **P1-3** temp_attachments 表 + 附件管理 — 中复杂度
5. **P1-8** audit_log 审计日志 — 中复杂度，生产必备
6. **P1-5** knowledge upload/replace/preview — 中复杂度
7. **P1-7** duplicate_project — 中复杂度
8. **P2-2** RAG config endpoint — 低复杂度
9. **P2-1** SSE 流式 endpoint 骨架 — 高复杂度，核心但需上游
10. **P3-2** mcp_server.py — 中复杂度
11. **P3-3** 项目导出增强 — 中复杂度
12. **P3-1** cowork_bridge 骨架 — 中复杂度
13. **P3-4** 删除完善 — 低复杂度

## 执行计划

### Batch 1: instruction_engine + ProjectCreate (快速补齐)
- instruction_engine: restore_snapshot, delete_snapshot
- project_store: 新增方法
- daemon_server: 新增 RPC handlers
- routes.py: 新增 REST endpoints
- ProjectCreate: 扩展 instructions, template_id, default_agent_id, rag_config
- project_manager.create: 支持新字段
- Tests

### Batch 2: chat move/detach + temp_attachments
- chat_manager: move_chat, detach_chat
- temp_attachments 表 + store 方法
- knowledge_manager: 临时附件 CRUD
- daemon_server + routes.py 新增
- Tests

### Batch 3: audit_log + knowledge upload/replace + duplicate
- audit_log 表 + store 方法
- knowledge_manager: upload_file (落盘), replace_file (降级), preview_file
- project_manager: duplicate_project
- daemon_server + routes.py 新增
- Tests

### Batch 4: RAG config + SSE skeleton + mcp_server
- RAG config REST endpoints
- SSE 流式推理 endpoint 骨架
- mcp_server.py (7 tools)
- Tests

### Batch 5: cowork_bridge + export enhance + cleanup
- cowork_bridge.py 骨架
- 全项目导出增强
- 删除完善
- README 更新
