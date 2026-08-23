# Implementation Plan: MCP 协议化与智能体安全加固

**Branch**: `006-mcp-security` | **Date**: 2026-08-05 | **Spec**: [spec.md](./spec.md)

## Summary

将 MCP 工具调用从本地函数调用升级为 JSON-RPC 2.0 标准协议信封；为工单/订单查询增加终端用户双因子身份校验（手机号+后4位）；增加 Prompt 注入检测与安全事件记录；增加 MCP 工具熔断降级机制。对齐宪法原则 II（多租户隔离）与 V（MCP 真实协议不可砍）。

## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0, Redis, LangGraph, litellm（均现有）
**Storage**: MySQL（security_incidents 新表）, Redis（熔断状态 + 幂等键，复用现有）
**Testing**: pytest（TDD：先写契约测试再实现）
**Project Type**: Web application（后端，本 feature 不涉及前端）
**Constraints**: 不破坏现有 MCP 工具清单/权限矩阵/退款幂等；协议化对 Agent 调用方尽量透明
**Scale/Scope**: 修改 mcp_servers/ + mcp_service + 2 个 agent 节点；新增 security_service + circuit_breaker + security_incident 模型

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | 说明 |
|-----------|--------|------|
| I. Layered Boundaries | ✅ | 双因子校验在 MCP server 层，注入检测在 Service 层，Agent 只收集槽位不查 DB |
| II. Tenant Isolation | ✅ | 工具调用 tenant_id 仍来自 JWT（已修复），双因子校验附加 tenant_id 过滤 |
| III. Money & State Safety | ✅ | 协议化不破坏写操作幂等（client_request_id 保留），退款金额上限不变 |
| IV. E2E Acceptance | ✅ | 4 个 User Story 各有独立测试 + 异常分支（越权/注入/熔断/不匹配） |
| V. Depth Over Breadth | ✅ | MCP 真实协议是核心不可砍链路，本 feature 正中 |

## Project Structure

### Documentation (this feature)

```text
specs/006-mcp-security/
├── plan.md              # 本文件
├── research.md          # Phase 0 研究产出
├── data-model.md        # Phase 1 数据模型
├── quickstart.md        # Phase 1 端到端验证指南
├── contracts/           # Phase 1 接口契约
│   ├── mcp-protocol.md
│   └── security-api.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks 生成（本阶段不创建）
```

### Source Code (repository root)

```text
backend/app/
├── mcp_servers/
│   ├── base_server.py      # 新增：JSON-RPC 2.0 信封封装（build_request/build_success/build_error）
│   ├── ticket_server.py    # 修改：handle 改为 JSON-RPC 解析 + 双因子校验
│   └── order_server.py     # 修改：同上
├── services/
│   ├── mcp_service.py      # 修改：call_tool 走 JSON-RPC + 熔断检查
│   └── security_service.py # 新增：detect_prompt_injection + record_incident
├── models/
│   ├── security_incident.py # 新增：SecurityIncident ORM
│   └── __init__.py         # 修改：注册 SecurityIncident
├── agents/nodes/
│   ├── ticket_agent.py     # 修改：槽位增加 phone，缺失反问
│   └── order_agent.py      # 修改：同上
└── core/
    └── circuit_breaker.py  # 新增：熔断状态管理（Redis）

backend/tests/
├── contracts/
│   ├── test_mcp_protocol.py   # JSON-RPC 信封契约测试（TDD 先写）
│   └── test_security_api.py   # 双因子/注入检测契约测试
└── integration/
    └── test_mcp_security_e2e.py  # 端到端：越权/注入/熔断
```

**Structure Decision**: 复用现有 `backend/app/` 分层结构。新增 `base_server.py` 抽象 JSON-RPC 信封，`circuit_breaker.py` 抽象熔断，`security_service.py` 抽象注入检测--三者均为可复用的 Core/Service 层组件，符合宪法原则 I。

## Complexity Tracking

> 无宪法违例。新增抽象层理由如下：

| 新增抽象 | Why Needed | 更简替代被否原因 |
|---------|------------|-----------------|
| `base_server.py`（JSON-RPC 信封） | ticket/order server 都需分封/解封信封，复用避免重复 | 各 server 各自分封易不一致，违反 DRY |
| `circuit_breaker.py`（熔断管理） | 熔断状态机（closed/open/half-open）逻辑独立，可被任意工具复用 | 内联到 mcp_service 会使 call_tool 职责过载 |
| `security_service.py`（注入检测） | 安全逻辑独立于业务，可被 agent_service/未来其他入口复用 | 内联 agent_service 违反单一职责 |