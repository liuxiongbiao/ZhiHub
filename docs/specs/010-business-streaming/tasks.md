# Tasks: 业务闭环与流式输出

**Feature**: 010-business-streaming | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Mode**: TDD（每个 User Story 的契约测试在实现前）

## Phase 1: Setup

- [X] T001 [P] Add 7 config constants to `backend/app/config.py`（MODEL_WEIGHTS / STORAGE_DOC_LIMIT=50 / REFUND_TIMEOUT_DAYS=7 / REFUND_APPROVAL_THRESHOLD=1000 / DOC_VALIDITY_DAYS=90 / QUALITY_GARBAGE_THRESHOLD=0.3 / INVOICE_OVERDUE_DAYS=15）
- [X] T002 [P] Add `expires_at` + `quality_status` to `backend/app/models/knowledge_document.py` + alembic migration `backend/alembic/versions/d7_knowledge_lifecycle.py`（down_revision=d6_ticket_cat；expires_at DATETIME nullable；quality_status VARCHAR(20) default "ready"）

## Phase 2: User Story 1 - 退款撤销/分级审批/超时 (P1)

**Story Goal**: 退款可撤销 + 大额二次审批 + 7天超时取消
**Independent Test**: 撤销 pending->cancelled；大额 agent 批准仍 pending；超时自动取消

- [X] T003 [US1] Write contract test for refund cancel/分级 in `backend/tests/contracts/test_business_streaming.py`（TDD：撤销 pending->cancelled；已审批不可撤销 409；大额 agent 批准仍 pending；大额 admin 批准->refunded）
- [X] T004 [US1] Implement cancel_refund + approve_refund 分级 in `backend/app/services/order_service.py`（cancel: 乐观锁 WHERE status=pending -> cancelled；approve: amount>阈值+role=agent -> 保持 pending；amount>阈值+role=admin -> refunded）
- [X] T005 [US1] Add cancel endpoint in `backend/app/api/v1/order.py`（POST /refunds/{id}/cancel，校验 tenant + 状态）
- [X] T006 [US1] Add cleanup_overdue_refunds in `backend/app/tasks/cleanup_tasks.py`（退款 pending 超 7 天 -> cancelled，后台线程）
- [X] T007 [US1] Verify refund cancel/分级/超时（**需 Docker mysql**）

## Phase 3: User Story 2 - SSE 流式输出 (P1)

**Story Goal**: POST /message/stream 逐字流式输出
**Independent Test**: SSE 事件流逐字输出，首字 <2s，完成后落库

- [X] T008 [US2] Write contract test for SSE in `backend/tests/contracts/test_business_streaming.py`（追加：流式端点返回 text/event-stream；data: {chunk} 逐字；[DONE] 结束；非知识咨询走同步兜底）
- [X] T009 [US2] Implement POST /message/stream in `backend/app/api/v1/session.py`（StreamingResponse；router+检索非流式 -> litellm.completion stream=True yield SSE chunks -> [DONE]；完成后 save_message 落库；非知识咨询同步兜底）
- [X] T010 [US2] Verify SSE streaming（**需 Docker Milvus/ES + DeepSeek**）

## Phase 4: User Story 3 - 知识库生命周期 (P2)

**Story Goal**: 质量检测 + 到期摘除 + 存储上限
**Independent Test**: 乱码文档->pending_review；到期->摘除；超 50 文档->拒绝

- [X] T011 [US3] Write contract test for quality/expiry/storage in `backend/tests/contracts/test_business_streaming.py`（追加：乱码率>30%->pending_review；存储超 50->拒绝；到期摘除 is_latest=False）
- [X] T012 [US3] Implement quality check in `backend/app/tasks/knowledge_tasks.py`（切片后统计乱码率，超阈值 -> quality_status=pending_review 不进检索库）
- [X] T013 [US3] Implement expiry cleanup + storage limit in `backend/app/services/knowledge_service.py` + `backend/app/tasks/cleanup_tasks.py`（上传前 count 文档数校验上限；cleanup_expired_docs 到期 -> is_latest=False + Milvus/ES delete）
- [X] T014 [US3] Verify knowledge lifecycle（**需 Docker Milvus/ES**）

## Phase 5: User Story 4 - 计费补全 (P2)

**Story Goal**: 多模型折算 + CSV 导出 + 逾期降级
**Independent Test**: 不同模型折算后配额递减；CSV 含完整字段；逾期->降级

- [X] T015 [US4] Write contract test for weighting/export/overdue in `backend/tests/contracts/test_business_streaming.py`（追加：incr_usage 按 model 权重折算；CSV 含 session/intent/model/tokens/weighted；逾期 15 天->degraded 标记）
- [X] T016 [US4] Implement model weighting in `backend/app/services/billing_service.py`（incr_usage 加 model 参数，count += MODEL_WEIGHTS.get(model, 1.0)）
- [X] T017 [US4] Implement CSV export + overdue degradation in `backend/app/services/billing_service.py` + `backend/app/api/v1/billing.py` + `backend/app/services/agent_service.py`（export_usage CSV StreamingResponse；check_overdue 查 unpaid+超 15 天 -> Redis degraded 标记；handle_message 开头检查降级）
- [X] T018 [US4] Verify billing（**需 Docker mysql/redis**）

## Phase 6: Polish & Cross-Cutting

- [X] T019 Write e2e integration test in `backend/tests/integration/test_business_streaming_e2e.py`（**需 Docker**：4 场景 smoke）
- [X] T020 Run connectivity test（import app.main + 关键模块 + 冒烟）
- [X] T021 Update implementation_checklist.md marking 1.9/1.10/1.11/2.3 done + update connectivity_test_report.md

## Dependencies

```
Phase 1 (Setup: config + knowledge_document 加列) -> US1-US4
US1 (退款) 依赖 T001（config 阈值）；T005 依赖 T004
US2 (SSE) 独立
US3 (知识库) 依赖 T002（+expires_at/quality_status）
US4 (计费) 依赖 T001（MODEL_WEIGHTS）；T017 改 agent_service 与 009 记忆注入同文件需顺序
T007/T010/T014/T018/T019 需 Docker（到时提示用户启动）
全部 -> Phase 6 (Polish)
```

## Parallel Opportunities

- T001 || T002（config vs knowledge_document 不同文件）
- US1 (T003-T007) || US2 (T008-T010) || US4 (T015-T018) 不同文件可并行
- 注意 T017 与 009 T010 都改 agent_service.py 需顺序

## MVP Scope

US1（退款撤销/分级/超时--资金安全宪法 III）

## Implementation Strategy

MVP 先行（US1 验收：退款撤销+分级+超时）-> 增量 US2（SSE）-> US3（知识库）-> US4（计费）-> Polish。实现阶段不需 Docker；T007/T010/T014/T018/T019 验证阶段需 Docker，到时与 009 验证一起跑。