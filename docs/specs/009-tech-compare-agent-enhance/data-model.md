# Data Model: 技术栈对比与智能体增强

> 本 feature 不新增 MySQL 表。数据结构为 Milvus memory_collection（向量存储）、FC Agent（代码结构）、Agent 评测报告（文档）。

## Milvus memory_collection（长期记忆，新建）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INT64, auto_id, PK | 主键 |
| tenant_id | INT64 | 租户隔离（宪法 II） |
| user_id | INT64 | 用户归属 |
| fact_type | VARCHAR(50) | 事实类型（plan/phone/preference 等，upsert 键） |
| content | VARCHAR(2048) | 事实内容（如"企业版套餐"） |
| vector | FLOAT_VECTOR(1024) | bge-m3 向量（content 的 embedding） |
| created_at | DOUBLE | 时间戳 |

**索引**：vector IVF_FLAT + IP（与 knowledge_chunks 同）；**upsert 策略**：按 tenant_id+user_id+fact_type 先删旧同类型再插新（套餐升级覆盖旧值）。

**复用**：embedding_client.embed_texts（bge-m3）+ Milvus 连接管理（init_memory_collection / get_memory_collection）。

## Function Calling Agent（代码结构，非实体）

`ticket_agent_fc.py`，与 MCP ticket_agent 等价的工单 Agent：
- 定义 tools schema（get_ticket_status: {ticket_no, phone}）
- litellm.completion(tools=[...], tool_choice="auto") -> LLM 返回 tool_calls
- 解析 tool_calls -> 调 mcp_service.call_tool（**复用 006 双因子/权限/熔断**）
- 结果格式化（与 MCP 版一致）
- 缺 phone 反问（双因子逻辑一致）

无独立数据存储，工具调用复用 mcp_service（信封入口）。

## Agent 评测报告（写入 docs/agent_eval_report.md）

```text
# Agent 评测报告（009-tech-compare-agent-enhance）

## 意图识别准确率
- 正确数/总数 = X/Y = Z%

## 工具调用成功率
- 成功数/总数 = X/Y = Z%

## 详细（每条）
| # | 消息 | 预期意图 | 实际意图 | 预期工具 | 实际工具 | 意图✓ | 工具✓ |
```

测试集：~10 条 {message, expected_intent, expected_tool}，新建于 scripts/agent_eval.py 内或 docs/agent_eval_questions.md。

## 现有数据的影响

- `tickets`/`orders`：FC 查询复用现有 phone 双因子（006），不改表。
- `sessions`/`messages`：记忆注入不改表（记忆在 Milvus，注入到 Redis context）。
- 不动资金/状态机；FC 复用 006 幂等/权限/熔断。