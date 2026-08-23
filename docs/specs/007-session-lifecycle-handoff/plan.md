# Implementation Plan: 会话生命周期与转人工增强

**Branch**: `007-session-lifecycle-handoff` | **Date**: 2026-08-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-session-lifecycle-handoff/spec.md`

## Summary

补全会话生命周期与转人工增强的四块缺口：(1) 连续3轮可解决类意图未解决 / 累计15轮自动转人工（Redis 会话轮次计数）；(2) 30分钟空闲超时定时收尾 AGENT_HANDLING/WAITING_HUMAN -> RESOLVED -> CLOSED（后台线程定时任务）；(3) 转人工无在线坐席时留言自动建单（Ticket 加 category 字段）+ 会话 CLOSED 后 CSAT 满意度评价；(4) 终端用户级对话限流（复用 user_id 维度，对话端点加严 10次/分钟，429）。强化 HITL 核心链路（宪法 V）与多租户隔离（宪法 II）。

## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0, Redis, LangGraph, litellm（均现有）
**Storage**: MySQL（tickets 加 category 列；csat_evaluations 复用现有表）, Redis（轮次计数 / 在线坐席集合 / 限流计数，复用现有）
**Testing**: pytest（TDD：先写契约测试再实现；SQLite in-memory + fakeredis，与 006 同）
**Project Type**: Web application（后端，本 feature 不涉及前端表单）
**Constraints**: 不破坏现有转人工（should_interrupt）/认领/配额链路；定时任务用后台线程（不引入 celery beat）；CSAT 经 session 间接校验租户（csat_evaluations 无 tenant_id 列）
**Scale/Scope**: 修改 session_service/agent_service/human_service/auth_service/rate_limit_service；新增 cleanup_tasks + crud/csat + category 列迁移

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | 说明 |
|-----------|--------|------|
| I. Layered Boundaries | ✅ | 轮次计数在 session_service/agent_service，空闲清理在 tasks，限流在 middleware/service，CSAT 端点在 API 层；MCP/Agent 不直接操作轮次计数 |
| II. Tenant Isolation | ✅ | 轮次计数键、限流键、在线坐席集合、空闲收尾查询、留言建单均带 tenant_id；CSAT 经 session 归属校验 tenant（csat_evaluations 无 tenant_id，通过 session.tenant_id 间接隔离） |
| III. Money & State Safety | ✅ | 会话状态机跃迁（RESOLVED->CLOSED）校验合法性；本 feature 不涉及资金/写操作幂等 |
| IV. E2E Acceptance | ✅ | 4 个 User Story 各有 Independent Test + 异常分支（竞态/重复CSAT/限流绕过/误触） |
| V. Depth Over Breadth | ✅ | HITL 是四条不可砍链路之一，本 feature 补全转人工触发条件（轮次/空闲），正中核心叙事 |

## Project Structure

### Documentation (this feature)

```text
specs/007-session-lifecycle-handoff/
├── plan.md              # 本文件
├── research.md          # Phase 0 研究产出
├── data-model.md        # Phase 1 数据模型
├── quickstart.md        # Phase 1 端到端验证指南
├── contracts/           # Phase 1 接口契约
│   └── session-lifecycle-api.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks 生成
```

### Source Code (repository root)

```text
backend/app/
├── services/
│   ├── session_service.py     # 修改：轮次计数 incr/get/reset + 在线坐席 presence
│   ├── agent_service.py       # 修改：handle_message 末尾 incr 轮次 + 连续/累计阈值转人工
│   ├── human_service.py       # 修改：leave_message_if_no_agent 接入在线判定 + category=message
│   ├── auth_service.py        # 修改：login SADD 在线 / logout SREM 在线（role=agent）
│   └── rate_limit_service.py  # 修改：新增 check_message_rate_limit(user_id, 10/min)
├── tasks/
│   └── cleanup_tasks.py       # 新增：cleanup_idle_sessions（后台线程定时跑）
├── crud/
│   └── csat.py                # 新增：CsatEvaluation CRUD（create/get_by_session）
├── api/v1/
│   └── session.py             # 修改：POST /{id}/csat 端点 + 留言端点 + 消息端点限流
├── middlewares/
│   └── rate_limit_middleware.py  # 修改（可选）：或消息限流放端点层
├── models/
│   └── ticket.py              # 修改：加 category 字段
└── core/
    └── (redis.py 复用，新增 presence/轮次 helper 或放 session_service)

backend/alembic/versions/
└── d6_ticket_category.py      # 新增：tickets 加 category 列

backend/tests/
├── contracts/
│   └── test_session_lifecycle.py  # 轮次/限流/CSAT/留言 契约测试（TDD 先写）
└── integration/
    └── test_session_lifecycle_e2e.py  # 端到端：轮次转人工/空闲收尾/留言建单/CSAT/限流
```

**Structure Decision**: 复用现有 `backend/app/` 分层。轮次计数/presence 为 session_service 内的 Redis helper（高频读写、会话级 TTL，不单独建模块）；空闲清理为 tasks 层定时任务（与 knowledge consumer 同模式）；CSAT CRUD 独立（csat 模型已有）。符合宪法原则 I。

## Complexity Tracking

> 无宪法违例。新增抽象/字段理由如下：

| 新增抽象/改动 | Why Needed | 更简替代被否原因 |
|---------|------------|-----------------|
| Ticket.category 列 | 留言建单需区分留言工单与正常工单，可查询、支持未来分类 | 标题前缀【留言】不可查询、不规范；复用 priority 语义错 |
| 会话轮次计数（Redis） | 连续/累计轮次是转人工触发条件，高频读写需快速存储 | 进程内存多 worker 不共享；MySQL 高频读写不适合 |
| 在线坐席 presence 集合 | "无在线坐席"判定需要实时 presence，留言建单依赖 | 查 human_handling 会话把"在岗"等同"正处理会话"，刚登录未认领坐席被误判离线 |
| cleanup_tasks 定时任务 | 空闲超时收尾需周期性扫描，现有无定时清理 | 手动触发不可持续；celery beat 对 demo 过重 |