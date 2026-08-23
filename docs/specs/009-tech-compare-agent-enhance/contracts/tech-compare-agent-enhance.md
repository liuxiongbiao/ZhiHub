# Contract: 技术栈对比与智能体增强（FC 对比 / 长期记忆 / Agent 评测）

> 本契约定义 009 feature 的内部行为与脚本契约。

## 1. Function Calling 对比（US1）

**节点**: `app/agents/nodes/ticket_agent_fc.py` 的 `ticket_agent_fc_node(state)`

**契约**:
- 定义 tools schema（get_ticket_status: params {ticket_no: str, phone: str}）
- litellm.completion(model=deepseek, messages=context, tools=[get_ticket_status], tool_choice="auto", temperature=0)
- 若 LLM 返回 tool_calls：解析 -> 调 `mcp_service.call_tool("agent", build_request("get_ticket_status", {ticket_no, phone}), tenant_id=state.tenant_id)`（**复用 006 信封入口 + 双因子/权限/熔断**）-> 格式化结果
- 若 LLM 不返回 tool_calls（缺 phone 反问/兜底）：返回反问或兜底回复
- 结果格式与 MCP ticket_agent 一致（SC-001）

**模式切换**: `config.agent_tool_mode`（mcp|fc，默认 mcp）；graph.py build_graph 按 mode 注册 ticket_agent_node 或 ticket_agent_fc_node。


## 2. 长期记忆（US2）

**服务**: `app/services/memory_service.py`

**接口**:
- `extract_facts(user_message: str) -> list[dict]`：LLM 抽取关键事实 -> [{fact_type, content}]，无则空
- `save_memory(tenant_id, user_id, facts: list[dict])`：embed content -> upsert Milvus memory_collection（按 tenant_id+user_id+fact_type 先删旧再插新）
- `search_memory(tenant_id, user_id, query: str, top_k=3, threshold=0.5) -> list[str]`：embed query -> Milvus search -> 相似度≥threshold 的 content 列表

**调用点（agent_service.handle_message）**:
- 开头：`memories = search_memory(tenant_id, user_id, user_message)` -> 若有，prepend 到 context（system 角色"用户历史记忆: ..."）
- 末尾（回复后）：`facts = extract_facts(user_message); if facts: save_memory(...)`（异步/降级，失败不阻断）

**降级（FR-008）**: extract/save/search 任一失败 -> log warning 跳过，主流程正常。

## 3. Agent 评测（US3）

**脚本**: `backend/scripts/agent_eval.py`（独立运行）

**输入**: ~10 条 agent 专用测试集 {message, expected_intent, expected_tool}

**流程**:
```
for q in test_set(10):
    # 意图准确率
    router_result = router_node({messages:[{role:user,content:q.message}], ...})
    intent_ok = (router_result.intent == q.expected_intent)
    # 工具调用成功率
    agent_result = ticket_agent_node({messages, slots:router_result.slots, tenant_id, ...})
    tool_ok = (agent_result.actual_tool == q.expected_tool)
# 汇总 -> docs/agent_eval_report.md
```

**输出**: `docs/agent_eval_report.md`（意图准确率 + 工具调用成功率 + 每条详情）。

**降级**: per-题 LLM/工具失败 -> 标记 fail，继续，汇总含失败数。

## 4. 降级与隔离契约

| 组件 | 失败场景 | 行为 |
|---|---|---|
| FC agent | LLM 不返回 tool_call | 兜底回复 + 反问（双因子缺失时） |
| FC agent | 工具调用失败 | 复用 006 熔断/错误信封处理 |
| 记忆抽取 | LLM 异常 | 跳过抽取，不阻断 |
| 记忆检索 | Milvus 不可用 | 无记忆正常处理 |
| 记忆 upsert | 失败 | log warning，下次重试 |
| 评测 | LLM/工具失败 | 该题 fail，汇总含失败数 |

**租户隔离（宪法 II）**: 记忆 save/search 带 tenant_id+user_id 过滤；评测带 tenant_id；FC 复用 006 tenant_id 注入。