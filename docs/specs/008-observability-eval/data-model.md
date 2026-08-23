# Data Model: 可观测性与效果评测

> 本 feature 不新增 MySQL 表。数据结构为 Langfuse trace（外部存储）、RAGAS 报告（文档）、脱敏规则、MongoDB TTL 索引、Langfuse 配置。

## Langfuse Trace（自托管 Postgres 后端）

一次 Agent 执行的 span 树，由 `@observe()` 装饰器自动嵌套生成：

```
trace (name="agent.handle_message", metadata={session_id, tenant_id, user_id})
├── span "router"        (input=user_message, output={intent, confidence, sentiment, slots}, metadata={elapsed_ms})
├── span "retrieval"     (input=query, output={result_count, top_scores}, metadata={hyde_fallback})  # 仅知识咨询
├── span "agent"         (input=intent, output={current_agent, mcp_tool?}, metadata={elapsed_ms})
└── span "generation"    (input=context, output=answer_preview, metadata={sources_count, total_elapsed_ms})
```

| 字段 | 说明 |
|---|---|
| trace.name | "agent.handle_message" |
| trace.metadata | session_id / tenant_id / user_id（检索键） |
| span.input/output | 各段输入输出（已脱敏） |
| span.metadata | 耗时等 |

**注意**：所有 input/output 在上报前经 `mask_pii` 脱敏（手机号 138****0001）。

## RAGAS 评测报告（写入 docs/retrieval_comparison.md）

追加"RAGAS 量化指标"表（不删现有 Top1 定性表）：

| # | 类型 | 方法 | Faithfulness | Answer Relevancy | Context Recall |
|---|---|---|---|---|---|
| Q1 | 精确 | 纯向量 | 0.xx | 0.xx | 0.xx |
| Q1 | 精确 | 混合 | 0.xx | 0.xx | 0.xx |
| ... | ... | ... | ... | ... | ... |

**汇总**：每类（精确/语义/库外/混合）× 每方法的三指标均值；结论：混合 Context Recall - 纯向量 Context Recall ≥ 15pp。

数据来源：`retrieval_service.hybrid_search` 的 `vector_results`（纯向量 contexts）与 `hybrid_results`（混合 contexts）；answer 由 DeepSeek 生成；ground_truth 用 eval_questions.md"预期"列。

## 脱敏规则

`app/utils/desensitize.py` `mask_pii(text) -> str`：

| 输入 | 输出 |
|---|---|
| 13800000001 | 138****0001 |
| 我的手机是13912345678 | 我的手机是139****5678 |
| TK-20240001（订单/工单号） | TK-20240001（不脱敏，不匹配 1[3-9]\d{9}） |

正则：`1[3-9]\d{9}` 整词匹配（\b 边界），替换为前3 + `****` + 后4。单向不可逆。

## MongoDB TTL 索引

`agent_traces` collection：

| 索引 | 字段 | 选项 | 说明 |
|---|---|---|---|
| ttl_created_at | created_at | expireAfterSeconds = 7776000（90天） | MongoDB 后台自动删除超期 trace 文档 |

建索引时机：`trace_service` 模块首次调用 save_trace 时 `create_index`（幂等）。

## Langfuse 配置（.env / config.py）

| 配置项 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| langfuse_host | LANGFUSE_HOST | http://localhost:3000 | 自托管 Langfuse 地址 |
| langfuse_public_key | LANGFUSE_PUBLIC_KEY | "" | Langfuse project public key |
| langfuse_secret_key | LANGFUSE_SECRET_KEY | "" | Langfuse project secret key |

未配置 key 时，Langfuse SDK 降级 no-op（不报错、不上报），主流程不受影响。

## 现有数据的影响

- `agent_traces`（MongoDB）：加 TTL 索引（新建索引，不影响现有文档结构）；save_trace 加脱敏（影响新写入，旧数据不回填脱敏）。
- `retrieval_comparison.md`：追加 RAGAS 量化表，保留现有 Top1 定性表。
- `eval_questions.md`：复用现有 18 题，不改。
- 不动 MySQL 表、不动资金/状态机。