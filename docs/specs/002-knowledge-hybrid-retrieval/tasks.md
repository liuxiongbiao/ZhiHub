# Tasks: 知识库上传 + 混合检索引擎

**Input**: Design documents from [specs/002-knowledge-hybrid-retrieval/](.)

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: ✅ TDD 模式 — 每个 User Story 先写测试（确认 RED），再写实现（确认 GREEN）。

**Organization**: 任务按 User Story 分组，每个 Story 可独立实现和测试。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 所属 User Story（US1/US2/US3/US4）
- 包含完整文件路径

---

## Phase 1: Setup（基础设施初始化）

**Purpose**: Alembic 迁移 + Milvus/ES 初始化 + Embedding/Rerank API 客户端

- [ ] T001 实现 KnowledgeDocument ORM 模型 in `zhihub/backend/app/models/knowledge_document.py`，字段对齐 data-model.md
- [ ] T002 生成并执行 Alembic 迁移：`alembic revision --autogenerate -m "day3_knowledge_document"` + `alembic upgrade head`
- [ ] T003 [P] 实现 Embedding API 客户端 in `zhihub/backend/app/core/embedding_client.py` — 封装硅基流动 bge-m3 API（batch_size=16，指数退避重试 3 次）
- [ ] T004 [P] 实现 Rerank API 客户端 in `zhihub/backend/app/core/rerank_client.py` — 封装硅基流动 bge-reranker-v2-m3 API
- [ ] T005 [P] 实现 Milvus Collection 初始化 + ES Index 初始化 in `zhihub/backend/app/core/embedding_client.py`（补充 init_milvus_collection / init_es_index 函数）
- [ ] T006 [P] 实现 RabbitMQ 连接管理 in `zhihub/backend/app/core/rabbitmq.py` — 连接、Exchange/Queue 声明、发布/消费基础封装
- [ ] T007 实现知识库 Pydantic schemas in `zhihub/backend/app/schemas/knowledge.py` — 对齐 contracts/knowledge-api.yaml 的请求/响应结构

**Checkpoint**: 数据库表就绪，embedding/rerank 客户端可通过 pytest fixture 调用，Milvus + ES 可连接。

---

## Phase 2: Foundational（阻塞性前置任务）

**Purpose**: 所有 User Story 的共享依赖 — 文件解析、CRUD、Celery 配置、路由注册

- [ ] T008 [P] 实现 KnowledgeDocument CRUD in `zhihub/backend/app/crud/knowledge.py` — get_by_id / get_by_tenant / create / update_status / mark_not_latest
- [ ] T009 [P] 实现文件解析器 in `zhihub/backend/app/utils/file_parser.py` — 格式白名单校验、大小校验（≤10MB）、PDF/Word/Markdown 文本提取、段落+标题规则切片
- [ ] T010 配置 Celery Worker 加载知识库任务模块 in `zhihub/backend/celery_worker.py`（注册 knowledge_tasks）
- [ ] T011 在 `zhihub/backend/app/main.py` 注册 knowledge router（`/api/v1/knowledge`），确认路由挂载
- [ ] T012 [P] 实现前端 API 接口层 in `zhihub/frontend/src/api/knowledge.ts` — upload / list / rebuild / test 四个接口函数

**Checkpoint**: 共享基础设施就绪 — CRUD 可测试、文件解析可测试、路由已挂载、前端 API 封装完成。

---

## Phase 3: User Story 1 — 上传文档并完成索引 (Priority: P1) 🎯 MVP

**Goal**: 上传 PDF/Word/Markdown → 创建记录 → 异步处理 → 文档变为 ready

**Independent Test**: `curl POST /api/v1/knowledge/upload` → 轮询 list → 状态变为 ready 且 chunk_count > 0

### Tests for User Story 1 ⚠️

> **TDD: 先写测试，确认 RED，再写实现**

- [ ] T013 [P] [US1] 文件解析器单元测试 in `zhihub/backend/tests/unit/test_file_parser.py` — 覆盖：有效 PDF/Word/Markdown 解析、空文件/非支持格式/超限文件拒绝、切片数量验证
- [ ] T014 [P] [US1] 知识库 API 集成测试 in `zhihub/backend/tests/integration/test_knowledge_api.py` — 覆盖：上传成功返回 201、不支持格式 400、超限 400、未认证 401、列表查询（含状态筛选+分页）、重建索引 404/409
- [ ] T015 [P] [US1] 知识库异步任务集成测试 in `zhihub/backend/tests/integration/test_knowledge_tasks.py` — 覆盖：完整 pending→processing→ready 流转、Embedding API 失败降级为 failed、双写失败重试逻辑

### Implementation for User Story 1

- [ ] T016 [US1] 实现知识库 Service in `zhihub/backend/app/services/knowledge_service.py` — 上传校验（格式+大小）、创建文档记录（pending）、投递 RabbitMQ 消息、状态流转（processing→ready/failed）
- [ ] T017 [US1] 实现 Celery 异步处理任务 in `zhihub/backend/app/tasks/knowledge_tasks.py` — 消费队列 → 文件解析 → 批量 Embedding → 并行写 Milvus+ES（双写不一致重试 3 次）→ 更新文档状态
- [ ] T018 [US1] 实现知识库 API 路由 in `zhihub/backend/app/api/v1/knowledge.py` — POST /upload（上传+校验+调用 service）、GET /list（分页+状态筛选）、POST /{doc_id}/rebuild（重建索引）

**Checkpoint**: 上传一份文档 → 状态变为 ready → Milvus + ES 中均有切片数据 ✅

---

## Phase 4: User Story 2 — 混合检索并对比纯向量召回 (Priority: P1) 🎯 MVP

**Goal**: 检索测试台输入查询 → 返回纯向量 vs 混合检索对比结果

**Independent Test**: `curl POST /api/v1/knowledge/test` → 返回 vector_results 和 hybrid_results 两路不同结果；空知识库返回 empty_knowledge=true

### Tests for User Story 2 ⚠️

> **TDD: 先写测试，确认 RED，再写实现**

- [ ] T019 [P] [US2] 混合检索引擎单元测试 in `zhihub/backend/tests/unit/test_retrieval_service.py` — 覆盖：查询改写（指代补全）、向量召回、BM25 召回、RRF 融合排序、Rerank 精排、置信度判断+HyDE 兜底、空知识库分支、多租户过滤

### Implementation for User Story 2

- [ ] T020 [US2] 实现混合检索引擎 in `zhihub/backend/app/services/retrieval_service.py` — 🔴 核心算法：
  - `rewrite_query(history, current_query)` — 指代补全（LLM 调用）
  - `vector_recall(query, tenant_id, top_k=20)` — Milvus 向量召回
  - `bm25_recall(query, tenant_id, top_k=20)` — ES BM25 召回
  - `rrf_fuse(vector_results, bm25_results, k=60)` — RRF 融合
  - `rerank(query, candidates, top_k=5)` — bge-reranker 精排
  - `hybrid_search(query, tenant_id, history)` — 主编排函数（空知识库→直接返回；低置信度→HyDE 兜底）
- [ ] T021 [US2] 在 `zhihub/backend/app/api/v1/knowledge.py` 中补充 POST /test 检索测试台端点

**Checkpoint**: 检索测试台输入精确查询和语义查询 → 返回两路不同结果 → 能看到混合检索优于纯向量召回的差异 ✅

---

## Phase 5: User Story 3 — 文档列表与重新索引 (Priority: P2)

**Goal**: 前端列表页展示所有文档 + 按状态筛选 + 分页 + 重新索引按钮

### Implementation for User Story 3

- [ ] T022 [P] [US3] 实现前端文档列表页 in `zhihub/frontend/src/views/knowledge/KnowledgeList.vue` — 状态筛选下拉框、上传按钮、文档状态表格、重新索引按钮、分页
- [ ] T023 [P] [US3] 实现检索结果卡片组件 in `zhihub/frontend/src/components/trace/RetrievalResult.vue` — 来源文档名、片段文本、置信度分数、来源标签（vector/bm25/hybrid）
- [ ] T024 [P] [US3] 前端组件测试 in `zhihub/frontend/tests/components/KnowledgeList.spec.ts` — 列表渲染、状态筛选、分页交互

**Checkpoint**: 前端列表页可查看所有文档、按状态筛选、触发重新索引 ✅

---

## Phase 6: User Story 4 — 版本管理：旧版本不进检索库 (Priority: P2)

**Goal**: 重复上传同名文档时，旧版本 is_latest=false，检索只能搜到新版本内容

### Implementation for User Story 4

- [ ] T025 [US4] 在 `zhihub/backend/app/services/knowledge_service.py` 中补全版本管理逻辑 — 同名文件上传时：旧版本 is_latest→false、新版本 version+1、is_latest=true
- [ ] T026 [US4] 在 `zhihub/backend/app/services/retrieval_service.py` 中确保检索时过滤旧版本 — Milvus 侧添加 version 元数据过滤 + ES 侧 is_latest=true 过滤

**Checkpoint**: 上传同名文档 → 旧版本 is_latest=false → 检索只返回新版切片 ✅

---

## Phase 7: Polish & 跨模块联调

**Purpose**: 前端检索测试台、端到端验收

- [ ] T027 [P] 实现前端检索测试台 in `zhihub/frontend/src/views/knowledge/SearchTest.vue` — 查询输入框、纯向量结果列、混合检索结果列、HyDE 兜底标记、耗时显示
- [ ] T028 [P] 前端检索测试台组件测试 in `zhihub/frontend/tests/components/SearchTest.spec.ts` — 查询输入、两路结果对比展示、空知识库提示
- [ ] T029 全链路端到端验收 — 按 `zhihub/specs/002-knowledge-hybrid-retrieval/quickstart.md` 执行全部 5 个验收场景

**Checkpoint**: 完整流程跑通 — 上传 → 检索对比 → 版本管理 → 异常场景 ✅

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: 无依赖 — 可立即开始
- **Phase 2 (Foundational)**: 依赖 Phase 1 完成 — **阻塞所有 User Story**
- **Phase 3 (US1)**: 依赖 Phase 2 完成 — P1 🎯 MVP
- **Phase 4 (US2)**: 依赖 Phase 2 + Phase 3 完成（需要已入库的文档来测试检索）
- **Phase 5 (US3)**: 依赖 Phase 2 完成 — 可与 US1/US2 并行
- **Phase 6 (US4)**: 依赖 Phase 3 完成（依赖 knowledge_service 版本管理逻辑）
- **Phase 7 (Polish)**: 依赖 US1-US4 全部完成

### User Story Dependencies

- **US1 (P1)**: 可独立完成 — 文档上传+异步处理是最小可用单元
- **US2 (P1)**: 需要 US1 的数据（ready 文档）来做检索测试，但代码本身可并行实现
- **US3 (P2)**: 可独立完成 — 仅依赖 CRUD 和 API 路由
- **US4 (P2)**: 依赖 US1 的 knowledge_service

### Within Each User Story

- TDD 模式: 测试 RED → 实现 GREEN → 验证 REFACTOR
- Models → Services → API Routes（对于后端）
- Core logic → Route binding（对于各模块）

### Parallel Opportunities

```text
Phase 1 并行组:
  T003 (embedding_client) + T004 (rerank_client) + T005 (Milvus/ES init) + T006 (rabbitmq)

Phase 2 并行组:
  T008 (CRUD) + T009 (file_parser) + T012 (frontend API)

Phase 3 测试并行组:
  T013 (test_file_parser) + T014 (test_knowledge_api) + T015 (test_knowledge_tasks)

Phase 3+4 实现可部分并行:
  T022 (US3 前端) + T023 (US3 组件) 可与 T016-T021 同步进行
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Phase 1: Setup（T001-T007）
2. Phase 2: Foundational（T008-T012）
3. Phase 3: US1 上传+异步处理（T013-T018）→ **验证上传链路**
4. Phase 4: US2 混合检索（T019-T021）→ **验证检索链路**
5. **STOP & VALIDATE**: 这是 Day 3 的最小可用版本——上传文档 + 检索对比

### Incremental Delivery

1. US1 + US2 → MVP（核心叙事：混合检索优于纯向量）
2. +US3 → 前端可用版本（列表管理 + 检索测试台）
3. +US4 → 完整版本（版本管理）
4. Phase 7 → 端到端验收通过

---

## Summary

| 指标 | 值 |
|---|---|
| 总任务数 | 29 |
| Phase 1 (Setup) | 7 tasks |
| Phase 2 (Foundational) | 5 tasks |
| Phase 3 (US1 - 上传+异步) | 6 tasks (3 tests + 3 impl) |
| Phase 4 (US2 - 混合检索) | 3 tasks (1 test + 2 impl) |
| Phase 5 (US3 - 列表+重建) | 3 tasks (2 impl + 1 test) |
| Phase 6 (US4 - 版本管理) | 2 tasks |
| Phase 7 (Polish) | 3 tasks |
| 可并行任务 | 14 (标记 [P]) |
| MVP 范围 | Phase 1-4 (US1+US2), 21 tasks |
