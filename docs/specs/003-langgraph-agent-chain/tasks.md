# Tasks: LangGraph 智能体主链路骨架

**Input**: [specs/003-langgraph-agent-chain/](.)

**Tests**: ✅ TDD 模式

## Phase 1: Setup (基础设施)

- [ ] T001 [P] 实现 Session ORM in `zhihub/backend/app/models/session.py`
- [ ] T002 [P] 实现 Message ORM in `zhihub/backend/app/models/message.py`
- [ ] T003 生成+执行 Alembic migration for sessions + messages 表
- [ ] T004 [P] 实现 Token 估算工具 in `zhihub/backend/app/utils/tokenizer.py`
- [ ] T005 [P] 实现 Session schemas in `zhihub/backend/app/schemas/session.py`（对齐 contracts/）
- [ ] T006 [P] 实现前端类型定义 in `zhihub/frontend/src/types/session.d.ts`

## Phase 2: Foundational (CRUD + Service + API)

- [ ] T007 [P] 实现 Session CRUD in `zhihub/backend/app/crud/session.py`
- [ ] T008 [P] 实现 Session Service in `zhihub/backend/app/services/session_service.py`
- [ ] T009 实现 Session API 路由 in `zhihub/backend/app/api/v1/session.py`（create/list/detail/message）
- [ ] T010 在 main.py 注册 session router + models/__init__.py 导入新模型

## Phase 3: US1+US2 — LangGraph 图 + Router + Knowledge Agent (P1) 🎯

- [ ] T011 [P] 🔴 实现 Router prompt in `zhihub/backend/app/agents/prompts/router.txt`（few-shot 4 例）
- [ ] T012 [P] 🔴 实现 Knowledge prompt in `zhihub/backend/app/agents/prompts/knowledge.txt`（防幻觉+引用格式）
- [ ] T013 🔴 实现 Router 节点 in `zhihub/backend/app/agents/nodes/router.py`
- [ ] T014 🔴 实现 Knowledge Agent 节点 in `zhihub/backend/app/agents/nodes/knowledge_agent.py`
- [ ] T015 🔴 实现 LangGraph 状态图 in `zhihub/backend/app/agents/graph.py`（State + 节点注册 + 条件边 + 编译）
- [ ] T016 🟢 更新 ticket_agent / order_agent 占位节点 in `zhihub/backend/app/agents/nodes/ticket_agent.py` / `order_agent.py`
- [ ] T017 🔴 实现 Agent Service in `zhihub/backend/app/services/agent_service.py`

## Phase 4: US3 — 前端 Widget + 会话管理 (P2)

- [ ] T018 [P] 实现前端 API 层 in `zhihub/frontend/src/api/session.ts`
- [ ] T019 实现 ChatWindow in `zhihub/frontend/src/components/chat/ChatWindow.vue`
- [ ] T020 [P] 实现 MessageBubble in `zhihub/frontend/src/components/chat/MessageBubble.vue`
- [ ] T021 实现 WidgetPreview in `zhihub/frontend/src/views/widget-demo/WidgetPreview.vue`

## Phase 5: TDD Tests

- [ ] T022 [P] 单元测试: graph + router in `zhihub/backend/tests/unit/test_graph.py`
- [ ] T023 [P] 单元测试: tokenizer in `zhihub/backend/tests/unit/test_tokenizer.py`
- [ ] T024 [P] 集成测试: session API in `zhihub/backend/tests/integration/test_session_api.py`

## Phase 6: Polish

- [ ] T025 端到端验收 — 按 quickstart.md 执行 4 个场景

**Summary**: 25 tasks | MVP: Phase 1-3 (T001-T017) | 🔴 核心: T011-T015, T017
