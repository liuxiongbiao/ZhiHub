# Implementation Plan: 技术栈对比与智能体增强

**Branch**: `009-tech-compare-agent-enhance` | **Date**: 2026-08-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-tech-compare-agent-enhance/spec.md`

## Summary


## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: FastAPI, LangGraph, litellm（现有）；Milvus（现有，复用 embedding_client）；DeepSeek（LLM，FC + 记忆抽取 + 评测）
**Storage**: Milvus（新建 memory_collection）；无新 MySQL 表
**Testing**: pytest（TDD；SQLite+fakereds for 单测；US2/US3 验证需 Docker Milvus + LLM）
**Project Type**: Web application（后端 + 独立评测脚本）
**Constraints**: FC 路径必须复用 006 安全加固（双因子/权限/熔断），不绕过；长期记忆/评测失败降级不阻断主流程；不破坏现有 MCP 路径

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | 说明 |
|-----------|--------|------|
| I. Layered Boundaries | ✅ | FC agent 在 Agent 层（与 MCP ticket_agent 同层）；记忆 service 在 Service 层；评测脚本独立；MCP/Agent 不直接操作记忆存储 |
| II. Tenant Isolation | ✅ | 记忆按 tenant_id+user_id 过滤（Milvus partition/expr）；评测带 tenant_id；FC 复用 006 tenant_id 注入 |
| III. Money & State Safety | ✅ | FC 复用 006 幂等/权限/熔断；记忆只读+写 Milvus（非资金）；评测离线 |
| IV. E2E Acceptance | ✅ | 3 个 User Story 各有 Independent Test + 异常分支（FC 不返回 tool_call/记忆空/评测 LLM 失败） |
| V. Depth Over Breadth | ✅ | MCP 选型对比（FC）是核心叙事增强；长期记忆是智能体技术深度；正中原则 V |

## Project Structure

### Documentation (this feature)

```text
specs/009-tech-compare-agent-enhance/
├── plan.md              # 本文件
├── research.md          # Phase 0 研究产出
├── data-model.md        # Phase 1 数据模型
├── quickstart.md        # Phase 1 端到端验证指南
├── contracts/           # Phase 1 接口契约
│   └── tech-compare-agent-enhance.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks 生成
```

### Source Code (repository root)

```text
backend/app/
├── agents/nodes/
│   └── ticket_agent_fc.py   # 新增：Function Calling 版工单 Agent（复用 006 双因子/权限/熔断）
├── services/
│   ├── memory_service.py    # 新增：长期记忆 save_memory/search_memory（Milvus）
│   └── agent_service.py     # 修改：handle_message 注入长期记忆 + 记忆抽取
├── agents/graph.py          # 修改：按 AGENT_TOOL_MODE 切换 ticket_agent / ticket_agent_fc
├── core/
│   └── embedding_client.py  # 修改：加 memory_collection init/get（复用 embed_texts）
├── config.py                # 修改：加 agent_tool_mode（mcp|fc，默认 mcp）
backend/scripts/
└── agent_eval.py            # 新增：Agent 评测脚本（~10 条专用集 -> agent_eval_report.md）
backend/tests/
├── contracts/
│   └── test_tech_compare.py  # FC 对比/记忆/评测 契约测试（TDD）
└── integration/
    └── test_tech_compare_e2e.py
zhihub/docs/
└── agent_eval_report.md     # 新增：Agent 评测报告（脚本产出）
```

**Structure Decision**: 复用现有分层。FC agent 与 MCP ticket_agent 同层（Agent 节点），记忆为 Service 层（memory_service），评测为 scripts 独立。符合宪法原则 I。

## Complexity Tracking

> 无宪法违例。新增抽象理由如下：

| 新增抽象 | Why Needed | 更简替代被否原因 |
|---------|------------|-----------------|
| ticket_agent_fc.py | FC 与 MCP 工具分发机制本质不同（LLM tools vs 协议信封），需独立节点 | 合并到一个节点加分支会使工具分发逻辑耦合，违反单一职责 |
| memory_service.py | 长期记忆 CRUD+检索独立于业务，可复用 | 内联 agent_service 职责过载 |
| memory_collection（Milvus） | 长期记忆需向量检索，复用 Milvus 基建 | MySQL 无法向量检索；Redis 不适合持久化记忆 |
| agent_eval.py | 评测离线脚本，不入请求路径 | 内联测试不可复现 |