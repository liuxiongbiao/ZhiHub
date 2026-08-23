# Tasks: MCP 协议化与智能体安全加固

**Feature**: 006-mcp-security | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Mode**: TDD（每个 User Story 的契约测试在实现前）

## Phase 1: Setup

- [X] T001 Create SecurityIncident ORM model in `backend/app/models/security_incident.py`（id/tenant_id/session_id/user_message_preview/rule_hit/risk_level/action_taken/created_at）
- [X] T002 Register SecurityIncident in `backend/app/models/__init__.py` + verify `init_db()` creates table

## Phase 2: Foundational（阻塞前提，所有 User Story 依赖）

- [X] T003 [P] Implement JSON-RPC 2.0 envelope helpers in `backend/app/mcp_servers/base_server.py`（build_request/build_success/build_error/parse_request + 7 错误码）
- [X] T004 [P] Implement circuit breaker state manager in `backend/app/core/circuit_breaker.py`（Redis 三态 closed/open/half-open，5 次失败熔断，30s half-open，3 次成功解除）

## Phase 3: User Story 1 - 终端用户双因子校验 (P1)

**Story Goal**: 查询工单/订单前校验手机号+后4位，仅手机号拒绝
**Independent Test**: 仅手机号查询被拒；不匹配返回"未查询到匹配信息"；匹配返回详情

- [X] T005 [US1] Write contract test for end-user identity verification in `backend/tests/contracts/test_security_api.py`（TDD：仅 ticket_no 无 phone 反问；phone 不匹配返回 -32602；匹配返回结果）
- [X] T006 [US1] Add phone verification to ticket_server in `backend/app/mcp_servers/ticket_server.py`（get_ticket_status 校验 phone 后4位与工单绑定手机号，不匹配返回"未查询到匹配信息"）
- [X] T007 [US1] Add phone verification to order_server in `backend/app/mcp_servers/order_server.py`（query_order 同理，校验 orders.phone）
- [X] T008 [US1] Add phone slot to ticket_agent in `backend/app/agents/nodes/ticket_agent.py`（槽位 {ticket_no, phone}，缺 phone 反问，双齐才调工具）
- [X] T009 [US1] Add phone slot to order_agent in `backend/app/agents/nodes/order_agent.py`（同上）
- [X] T010 [US1] Update router prompt to include phone slot in `backend/app/agents/prompts/router.txt`（槽位说明增加 phone）

## Phase 4: User Story 2 - Agent 标准协议调用 (P1)

**Story Goal**: 工具调用走 JSON-RPC 2.0 信封，非本地函数调用
**Independent Test**: 请求/响应遵循信封结构；非法方法名返回标准错误

- [X] T011 [US2] Write contract test for JSON-RPC envelope in `backend/tests/contracts/test_mcp_protocol.py`（TDD：成功响应含 jsonrpc/id/result；非法方法返回 -32601；参数错返回 -32602）
- [X] T012 [US2] Refactor mcp_service.call_tool to use envelope in `backend/app/services/mcp_service.py`（解封请求 -> 权限/入参/幂等校验 -> 调 server -> 封装响应；tenant_id 注入覆盖客户端）
- [X] T013 [US2] Update mcp server endpoints to return envelope in `backend/app/api/v1/mcp/server.py`（/tools/call 返回 JSON-RPC 信封；tenant_id 注入 params）
- [X] T014 [US2] Update /tools/list to return envelope in `backend/app/api/v1/mcp/server.py`

## Phase 5: User Story 3 - Prompt 注入检测 (P2)

**Story Goal**: 用户输入进 Agent 前检测注入，命中转人工+记录事件
**Independent Test**: 注入话术转人工；正常查询不误判

- [X] T015 [US3] Write contract test for injection detection in `backend/tests/contracts/test_security_api.py`（追加：ignore_instruction 命中 high；正常查询 allow）
- [X] T016 [US3] Implement security_service in `backend/app/services/security_service.py`（detect_prompt_injection 规则库 + record_incident 写 security_incidents）
- [X] T017 [US3] Integrate injection check into agent_service in `backend/app/services/agent_service.py`（handle_message 开头调检测，high 转人工+记录，不进 graph）

## Phase 6: User Story 4 - MCP 熔断降级 (P2)

**Story Goal**: 工具连续5次空值/异常熔断，兜底响应，3次恢复解除
**Independent Test**: 连续失败触发熔断返回 -32001；恢复后解除

- [X] T018 [US4] Write contract test for circuit breaker in `backend/tests/contracts/test_mcp_protocol.py`（追加：连续5次空 result 触发熔断；熔断中返回 -32001；half-open 3 次成功解除）
- [X] T019 [US4] Integrate circuit breaker into mcp_service in `backend/app/services/mcp_service.py`（call_tool 前检查熔断状态；调用后监控关键字段空值，失败 fail_count++，成功在 half_open 时 recover_count++）
- [X] T020 [US4] Add fallback static response for circuit-broken tools in `backend/app/services/mcp_service.py`（熔断中返回兜底话术 + 提示转人工）

## Phase 7: Polish & Cross-Cutting

- [X] T021 Write end-to-end integration test in `backend/tests/integration/test_mcp_security_e2e.py`（4 场景：双因子越权/协议信封/注入拦截/熔断，对照 quickstart.md）
- [X] T022 Run connectivity test（import app.main + 关键模块 + 端到端冒烟，确保无断链）
- [X] T023 Update implementation_checklist.md marking 1.1/1.2/1.3/1.8 done + update connectivity_test_report.md

## Dependencies

```
Phase 1 (Setup) -> Phase 2 (Foundational)
Phase 2 -> Phase 3 (US1) + Phase 4 (US2)
US1 + US2 -> Phase 5 (US3) + Phase 6 (US4)
全部 -> Phase 7 (Polish)
```

## Parallel Opportunities

- T003 || T004（base_server 与 circuit_breaker 不同文件）
- T006 || T007（ticket_server 与 order_server）
- T008 || T009（ticket_agent 与 order_agent）
- US3 (T015-T017) || US4 (T018-T020)

## MVP Scope

US1 + US2（P1 双因子 + 标准协议）

## Implementation Strategy

MVP 先行（US1+US2 验收）-> 增量 US3/US4 -> Polish。每 Phase 完成跑连通性测试。
