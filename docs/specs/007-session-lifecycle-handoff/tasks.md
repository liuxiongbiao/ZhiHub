# Tasks: 会话生命周期与转人工增强

**Feature**: 007-session-lifecycle-handoff | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Mode**: TDD（每个 User Story 的契约测试在实现前）

## Phase 1: Setup

- [X] T001 Add `category` column to Ticket in `backend/app/models/ticket.py` + alembic migration `backend/alembic/versions/d6_ticket_category.py`（down_revision=d5_ticket_phone，VARCHAR(30) nullable default "general"；现有 tickets 表需 ALTER 加列，与 006 phone 列同方式）

## Phase 2: User Story 1 - 轮次触发转人工 (P1)

**Story Goal**: 连续3轮可解决类意图未解决 / 累计15轮 -> 自动转人工
**Independent Test**: 同会话连发3次工单查询 -> 转人工；累计15轮 -> 转人工；知识咨询不计入 consecutive

- [X] T002 [US1] Write contract test for round-based handoff in `backend/tests/contracts/test_session_lifecycle.py`（TDD：连续3轮可解决意图相同 -> 转人工；累计15轮 -> 转人工；知识咨询不计入 consecutive）
- [X] T003 [US1] Implement session rounds counter in `backend/app/services/session_service.py`（Redis `session:rounds:{sid}` -> {total, consecutive, last_intent}；incr_rounds/get_rounds/reset_rounds；TTL 30min 滚动续期；可解决类意图相同才 consecutive++，知识咨询豁免）
- [X] T004 [US1] Integrate rounds check into agent_service in `backend/app/services/agent_service.py`（handle_message 末尾 incr 轮次；consecutive>=3 或 total>=15 -> should_interrupt=True + 转人工提示；坐席退回时 reset consecutive）

## Phase 3: User Story 2 - 空闲超时收尾 (P1)

**Story Goal**: 30min 无活动 AGENT_HANDLING/WAITING_HUMAN -> RESOLVED；再 30min -> CLOSED
**Independent Test**: 模拟 updated_at 31min 前 -> 定时任务转 RESOLVED；RESOLVED 61min -> CLOSED；活跃会话不误收尾

- [X] T005 [US2] Write contract test for idle cleanup in `backend/tests/contracts/test_session_lifecycle.py`（追加：AGENT_HANDLING 30min -> RESOLVED；RESOLVED 60min -> CLOSED；updated_at 新鲜的会话被跳过防竞态）
- [X] T006 [US2] Implement cleanup_idle_sessions in `backend/app/tasks/cleanup_tasks.py`（扫描 status in(agent_handling,waiting_human) 且 updated_at<now-30min -> resolved；status=resolved 且 updated_at<now-60min -> closed；带 tenant_id 过滤 + id+updated_at 乐观条件防竞态）
- [X] T007 [US2] Launch cleanup background thread in `backend/app/main.py`（startup 起后台线程每 5min 跑 cleanup_idle_sessions，与 knowledge consumer 同模式）

## Phase 4: User Story 3 - 留言建单 + CSAT (P2)

**Story Goal**: 转人工无在线坐席 -> 留言建单(category=message)；CLOSED 后 CSAT 评价
**Independent Test**: 无坐席留言 -> 建单 category=message；有坐席 -> 409；CSAT closed -> 201，重复 -> 409，非 closed -> 403

- [X] T008 [US3] Write contract test for leave-message + CSAT in `backend/tests/contracts/test_session_lifecycle.py`（追加：无坐席留言 -> 建单 category=message；有坐席 -> 409；CSAT closed -> 201；重复 -> 409；非 closed -> 403；跨租户 -> 403/404）
- [X] T009 [P] [US3] Implement agent presence in `backend/app/services/auth_service.py` + `backend/app/services/session_service.py`（login role=agent -> SADD `agents:online:{tenant}` user_id；logout -> SREM；新增 has_online_agent(tenant_id) -> SCARD==0）
- [X] T010 [P] [US3] Create CSAT CRUD in `backend/app/crud/csat.py`（create_csat(session_id, score, comment)；get_by_session(session_id) 去重判定）
- [X] T011 [US3] Wire leave_message_if_no_agent to presence + category in `backend/app/services/human_service.py`（has_online_agent 判定；建工单 category="message"；依赖 T009）
- [X] T012 [US3] Add leave-message endpoint in `backend/app/api/v1/session.py`（POST /{id}/leave-message；校验 session 归属 tenant + 状态 waiting_human；无在线坐席才建单；有坐席 -> 409）
- [X] T013 [US3] Add CSAT endpoint + schema in `backend/app/api/v1/session.py` + `backend/app/schemas/session.py`（POST /{id}/csat；CSATRequest{score:1-5, comment?}；校验 closed + tenant + 不可重复；依赖 T010）

## Phase 5: User Story 4 - 终端用户对话限流 (P2)

**Story Goal**: 单用户 10次/分钟对话限流，超限 429 不落库
**Independent Test**: 第11条/分钟 -> 429；不同用户互不影响；限流不落库不计轮次

- [X] T014 [US4] Write contract test for message rate limit in `backend/tests/contracts/test_session_lifecycle.py`（追加：10条通过，第11条 429；不写 messages 表、不计轮次；不同用户独立计数）
- [X] T015 [US4] Implement check_message_rate_limit in `backend/app/services/rate_limit_service.py`（键 `rate:msg:{user_id}:minute`，ZSET 滑动窗口，10/min，返回 {allowed, count}）
- [X] T016 [US4] Wire message rate limit into session.py in `backend/app/api/v1/session/message`（端点层校验 check_message_rate_limit(user_id)；超限 HTTPException 429 + "您提问过于频繁，请稍后再试"；不落库不计轮次）

## Phase 6: Polish & Cross-Cutting

- [X] T017 Write end-to-end integration test in `backend/tests/integration/test_session_lifecycle_e2e.py`（4 场景对照 quickstart：轮次转人工/空闲收尾/留言建单+CSAT/限流）
- [X] T018 Run connectivity test（import app.main + 关键模块 + 端到端冒烟，确保无断链）
- [X] T019 Update implementation_checklist.md marking 1.4/1.5/1.6/1.7 done + update connectivity_test_report.md

## Dependencies

```
Phase 1 (Setup category 列) -> Phase 4 (US3 留言建单用 category)
Phase 2 (US1) / Phase 3 (US2) / Phase 5 (US4) 互相独立，可并行
Phase 4 (US3) 依赖 Phase 1；T011 依赖 T009；T013 依赖 T010
全部 -> Phase 6 (Polish)
```

## Parallel Opportunities

- T009 || T010（auth_service/presence 与 crud/csat 不同文件）
- US1 (T002-T004) || US2 (T005-T007) || US4 (T014-T016) 不同文件可并行
- 注意：T012/T013/T016 都改 session.py，需顺序执行避免冲突

## MVP Scope

US1 + US2（P1 轮次转人工 + 空闲收尾）

## Implementation Strategy

MVP 先行（US1+US2 验收）-> 增量 US3/US4 -> Polish。每 Phase 完成跑连通性测试。