# Tasks: 技术栈对比与智能体增强

**Feature**: 009-tech-compare-agent-enhance | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Mode**: TDD（每个 User Story 的契约测试在实现前）

## Phase 1: Setup

- [X] T001 [P] Add `agent_tool_mode` config to `backend/app/config.py`（mcp|fc，默认 mcp，从 .env 读）
- [X] T002 [P] Add memory_collection init/get to `backend/app/core/embedding_client.py`（init_memory_collection + get_memory_collection，schema: id/tenant_id/user_id/fact_type/content/vector/created_at，复用 embed_texts + Milvus 连接）

## Phase 2: User Story 1 - Function Calling 对比 (P1)

**Story Goal**: FC 版工单 Agent（复用 006 双因子/权限/熔断），配置切换 MCP/FC，对比文档讲清选型
**Independent Test**: 切 FC 模式 -> 查工单（含双因子）-> 结果与 MCP 一致

- [X] T003 [US1] Write contract test for FC agent in `backend/tests/contracts/test_tech_compare.py`（TDD：FC tools schema 定义；LLM 返回 tool_calls -> 调 mcp_service.call_tool；缺 phone 反问；不返回 tool_call 兜底）
- [X] T004 [US1] Implement `backend/app/agents/nodes/ticket_agent_fc.py`（litellm.completion tools=[get_ticket_status]，解析 tool_calls -> 调 mcp_service.call_tool 复用 006 双因子/权限/熔断；结果格式与 MCP 一致；缺 phone 反问；不返回 tool_call 兜底）
- [X] T005 [US1] Wire FC node into `backend/app/agents/graph.py`（build_graph 按 settings.agent_tool_mode 注册 ticket_agent_node 或 ticket_agent_fc_node；默认 mcp）
- [X] T007 [US1] Verify FC mode e2e（**需 Docker mysql/redis + DeepSeek**；切 fc 模式发消息 -> 工单详情与 MCP 一致；缺 phone 反问）

## Phase 3: User Story 2 - 长期记忆 (P2)

**Story Goal**: Milvus 向量化记忆，跨会话记住用户偏好，handle_message 注入
**Independent Test**: 会话 A 偏好 -> 会话 B 召回体现

- [X] T008 [US2] Write contract test for memory_service in `backend/tests/contracts/test_tech_compare.py`（追加：extract_facts LLM 抽取；save_memory upsert；search_memory 阈值过滤；空/失败降级）
- [X] T009 [US2] Implement `backend/app/services/memory_service.py`（extract_facts 用 LLM 抽 {fact_type,content}；save_memory embed+upsert Milvus 按 tenant+user+fact_type 先删旧再插新；search_memory embed query + top-K + 相似度阈值；全降级）
- [X] T010 [US2] Integrate memory into `backend/app/services/agent_service.py`（handle_message 开头 search_memory 注入 context（system 角色）；末尾 extract_facts+save_memory（降级不阻断））
- [X] T011 [US2] Verify cross-session memory（**需 Docker Milvus + DeepSeek**；会话 A 表明偏好 -> 会话 B 召回体现；停 Milvus 降级正常）

## Phase 4: User Story 3 - Agent 评测 (P3)

**Story Goal**: ~10 条专用集，意图准确率 + 工具调用成功率，产出报告
**Independent Test**: 运行 agent_eval.py -> agent_eval_report.md 含两项指标

- [X] T012 [US3] Write contract test for agent_eval helpers in `backend/tests/contracts/test_tech_compare.py`（追加：测试集解析；意图准确率计算；工具成功率计算；报告格式化）
- [X] T013 [US3] Implement `backend/scripts/agent_eval.py`（新建 ~10 条 agent 专用集 {message,expected_intent,expected_tool}；跑 router 算意图准确率；跑 agent 算工具成功率；写 docs/agent_eval_report.md；per-题失败标记）
- [X] T014 [US3] Run agent_eval（**需 DeepSeek API**；跑脚本产真实评测数据，写 agent_eval_report.md）

## Phase 5: Polish & Cross-Cutting

- [X] T015 Write e2e integration test in `backend/tests/integration/test_tech_compare_e2e.py`（**需 Docker**：FC 查工单 + 跨会话记忆 + 评测 smoke）
- [X] T016 Run connectivity test（import app.main + ticket_agent_fc/memory_service/agent_eval + 冒烟）
- [X] T017 Update implementation_checklist.md marking 3.1/3.2/3.3 done + update connectivity_test_report.md

## Dependencies

```
Phase 1 (Setup: config + memory_collection) -> US1 / US2 / US3
US1 (FC) 依赖 T001（config）；T005 依赖 T004
US2 (记忆) 依赖 T002（memory_collection）；T010 依赖 T009
US3 (评测) 独立（复用现有 router/agent）；T014 依赖 T013
T007/T011/T014/T015 需 Docker/LLM（到时提示用户启动）
全部 -> Phase 5 (Polish)
```

## Parallel Opportunities

- T001 || T002（config.py 与 embedding_client.py 不同文件）
- T004 (ticket_agent_fc) || T009 (memory_service) 不同文件
- US1 (T003-T007) || US3 (T012-T014) 不同文件可并行

## MVP Scope


## Implementation Strategy

MVP 先行（US1 验收：FC 查工单与 MCP 一致 + 对比文档）-> 增量 US2（长期记忆）-> US3（评测）-> Polish。实现阶段不需 Docker；T007/T011/T014/T015 验证阶段需 Docker/LLM，到时提示用户启动。每 Phase 完成跑连通性测试。