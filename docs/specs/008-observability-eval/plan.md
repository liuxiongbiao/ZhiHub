# Implementation Plan: 可观测性与效果评测

**Branch**: `008-observability-eval` | **Date**: 2026-08-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-observability-eval/spec.md`

## Summary


## Technical Context

**Language/Version**: Python 3.14
**Primary Dependencies**: FastAPI, SQLAlchemy, LangGraph, litellm（现有）；**新增** `langfuse`（LLM 可观测 SDK）、`ragas`（RAG 评测，含 langchain 依赖）
**Storage**: MongoDB（agent_traces 加 TTL 90 天索引）；Langfuse 自托管（Postgres 后端，Docker）
**Testing**: pytest（TDD；SQLite+fakeredis for 单测；RAGAS/Langfuse 集成测试需 Docker + 真实 Milvus/ES）
**Project Type**: Web application（后端 + 独立评测脚本）
**Constraints**: Langfuse/RAGAS/脱敏均非关键路径，接入失败降级不阻断对话主流程（FR-010）；不破坏现有 MongoDB trace；RAGAS 评测脚本独立运行，不入请求路径
**Scale/Scope**: 新增 `langfuse_client.py` + `utils/desensitize.py` + `scripts/ragas_eval.py`；修改 `trace_service.py`（脱敏+TTL+Langfuse）、`agent_service.py`（Langfuse span）、`config.py`（Langfuse 配置）、`docker-compose.yml`（langfuse+postgres）、`requirements.txt`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | 说明 |
|-----------|--------|------|
| I. Layered Boundaries | ✅ | Langfuse 接入在 trace_service/agent_service（Service 层），脱敏在 utils（工具层），RAGAS 脚本独立（不入分层）；MCP/Agent 不感知 Langfuse |
| II. Tenant Isolation | ✅ | Langfuse trace 带 session_id/tenant_id/user_id；RAGAS 评测用 tenant_id 过滤检索；脱敏不涉及租户数据 |
| III. Money & State Safety | ✅ | 不涉及资金/写操作幂等；trace 只读+写 MongoDB/Langfuse，TTL 自动过期不破坏业务数据 |
| IV. E2E Acceptance | ✅ | 3 个 User Story 各有 Independent Test + 异常分支（Langfuse 不可用降级/空检索/LLM 评分失败） |

## Project Structure

### Documentation (this feature)

```text
specs/008-observability-eval/
├── plan.md              # 本文件
├── research.md          # Phase 0 研究产出
├── data-model.md        # Phase 1 数据模型
├── quickstart.md        # Phase 1 端到端验证指南
├── contracts/           # Phase 1 接口契约
│   └── observability-eval.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks 生成
```

### Source Code (repository root)

```text
backend/app/
├── core/
│   └── langfuse_client.py    # 新增：Langfuse SDK 初始化 + @observe 封装（降级安全）
├── utils/
│   └── desensitize.py        # 新增：mask_pii(text) 手机号脱敏（前3后4，单向）
├── services/
│   ├── trace_service.py      # 修改：save_trace 入库前 mask_pii；建 TTL 90天索引；写 Langfuse
│   └── agent_service.py      # 修改：handle_message 加 Langfuse trace + 四段 span
├── config.py                 # 修改：加 langfuse_host/public_key/secret_key
backend/scripts/
└── ragas_eval.py             # 新增：RAGAS 评测脚本（18题 × 2检索 × 3指标 -> retrieval_comparison.md）
backend/tests/
├── contracts/
│   └── test_observability_eval.py  # 脱敏/降级 契约测试（TDD）
└── integration/
    └── test_observability_e2e.py   # Langfuse/RAGAS e2e（需 Docker）
zhihub/deploy/
└── docker-compose.yml        # 修改：加 langfuse + postgres 服务
```

**Structure Decision**: 复用现有分层。Langfuse SDK 封装为 core 层（`langfuse_client.py`，全局单例 + 降级），脱敏为 utils 工具，RAGAS 为 scripts 独立脚本（不入应用分层，离线跑）。符合宪法原则 I。

## Complexity Tracking

> 无宪法违例。新增依赖/抽象理由如下：

| 新增依赖/抽象 | Why Needed | 更简替代被否原因 |
|---------|------------|-----------------|
| ragas 包 | PRD 3.12.2 要求 RAGAS 三指标量化对比 | 手工打分主观不可复现，无行业标准 |
| utils/desensitize.py | trace PII 脱敏是合规前提，复用于 MongoDB+Langfuse | 内联 trace_service 不可复用、违反 DRY |
| core/langfuse_client.py | Langfuse SDK 单例 + 降级封装，所有 trace 点统一入口 | 各处直接调 SDK 不一致且降级逻辑散落 |