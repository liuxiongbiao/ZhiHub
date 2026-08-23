# Tasks: 可观测性与效果评测

**Feature**: 008-observability-eval | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Mode**: TDD（每个 User Story 的契约测试在实现前）

## Phase 1: Setup

- [X] T001 Install `langfuse`/`ragas`/`langchain-openai` + add to `backend/requirements.txt`（pip install + 写入 requirements）
- [X] T002 [P] Add Langfuse config to `backend/app/config.py`（langfuse_host/public_key/secret_key，从 .env 读，默认空）
- [X] T003 [P] Add langfuse + postgres services to `zhihub/deploy/docker-compose.yml`（Langfuse v3 自托管最小集，端口 3000）

## Phase 2: User Story 1 - RAGAS 量化评测 (P1)

**Story Goal**: 对 18 题分别算纯向量/混合检索的 Faithfulness/AR/Context Recall，产出量化对比填入 retrieval_comparison.md
**Independent Test**: 运行 ragas_eval.py -> retrieval_comparison.md 追加 RAGAS 量化表 + 结论（混合 Context Recall - 纯向量 ≥15pp）

- [X] T004 [US1] Write contract test for RAGAS eval helpers in `backend/tests/contracts/test_observability_eval.py`（TDD：eval_questions.md 解析为 18 题、RAGAS dataset 构造、结果格式化；不含真实 RAGAS 运行，避免 Docker 依赖）
- [X] T005 [US1] Implement `backend/scripts/ragas_eval.py`（解析 18 题 -> 调 hybrid_search 取 vector_results/hybrid_results 作两路 contexts -> DeepSeek 生成 answer -> RAGAS 算 Faithfulness/AR/CR -> 写 docs/retrieval_comparison.md 追加量化表 + 结论；per-题失败标记 fail）
- [X] T006 [US1] Run RAGAS eval + fill `docs/retrieval_comparison.md`（**需 Docker Milvus/ES**；跑脚本产真实量化数据，验证混合 Context Recall - 纯向量 ≥15pp）

## Phase 3: User Story 2 - Langfuse 全链路 trace (P1)

**Story Goal**: 每次 Agent 执行的 router/retrieval/agent/generation 四段 span 在 Langfuse UI 可查
**Independent Test**: 发一条知识消息 -> Langfuse UI 看到完整 4 段 trace + 耗时

- [X] T007 [US2] Write contract test for Langfuse client 降级 in `backend/tests/contracts/test_observability_eval.py`（追加：未配置 key -> get_langfuse 返回降级 no-op；observe 装饰器不抛异常；已配置走真实分支）
- [X] T008 [P] [US2] Implement `backend/app/core/langfuse_client.py`（get_langfuse 单例，未配置/失败返回 None；observe 装饰器封装，无 Langfuse 时函数仍正常执行；import 失败降级）
- [X] T009 [US2] Integrate Langfuse trace into `backend/app/services/agent_service.py` + `backend/app/services/trace_service.py`（handle_message @observe 顶层 trace + router/retrieval/agent/generation 四段 span；metadata 带 session_id/tenant_id/user_id；上报失败降级 log warning）
- [X] T010 [US2] Verify Langfuse UI trace（**需 Docker langfuse+postgres**；启动 Langfuse -> UI 创建 project 取 key 配 .env -> 发消息 -> 浏览器看 4 段 trace）

## Phase 4: User Story 3 - trace PII 脱敏 + 90 天 TTL (P2)

**Story Goal**: trace 入库前手机号脱敏（138****0001）；MongoDB trace 90 天 TTL
**Independent Test**: 发含手机号消息 -> MongoDB/Langfuse trace 手机号脱敏；agent_traces 有 TTL 索引

- [X] T011 [US3] Write contract test for mask_pii in `backend/tests/contracts/test_observability_eval.py`（追加：13800000001->138****0001；TK-20240001/ORD-20240001 不误伤；空/None/无手机号文本安全）
- [X] T012 [P] [US3] Implement `backend/app/utils/desensitize.py`（mask_pii，正则 `1[3-9]\d{9}` 整词匹配，前3+****+后4，单向不可逆；失败 fallback 截断）
- [X] T013 [US3] Integrate mask_pii + TTL into `backend/app/services/trace_service.py`（save_trace 入库前 mask query/answer_preview；Langfuse 上报前同样 mask；首次调用建 agent_traces created_at TTL 索引 expireAfterSeconds=7776000）

## Phase 5: Polish & Cross-Cutting

- [X] T014 Write e2e integration test in `backend/tests/integration/test_observability_e2e.py`（**需 Docker**：发消息 -> Langfuse 4 段 trace + 脱敏 + Langfuse 不可用降级）
- [X] T015 Run connectivity test（import app.main + langfuse_client/desensitize/trace_service + 脱敏冒烟）
- [X] T016 Update implementation_checklist.md marking 2.1/2.2/2.4 done + update connectivity_test_report.md

## Dependencies

```
Phase 1 (Setup: install + config + compose) -> US1 / US2 / US3
US1 (RAGAS) 独立（复用现有 hybrid_search，不改检索层）
US2 (Langfuse) 依赖 T001/T002（langfuse 包 + config）；T009 改 trace_service
US3 (脱敏+TTL) 独立；T013 也改 trace_service（与 T009 顺序执行避免冲突）
T006/T010/T014 需 Docker（到时提示用户启动）
全部 -> Phase 5 (Polish)
```

## Parallel Opportunities

- T002 || T003（config.py 与 docker-compose.yml 不同文件）
- T008 || T012（langfuse_client.py 与 desensitize.py 不同文件）
- US1 (T004-T006) 与 US2/US3 不同文件可并行；注意 T009/T013 都改 trace_service.py 需顺序

## MVP Scope


## Implementation Strategy

MVP 先行（US1 验收：量化对比数据产出）-> 增量 US2（Langfuse 可观测）-> US3（脱敏+TTL 合规）-> Polish。实现阶段不需 Docker；T006/T010/T014 验证阶段需 Docker，到时提示用户启动。每 Phase 完成跑连通性测试。