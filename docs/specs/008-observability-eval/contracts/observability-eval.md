# Contract: 可观测性与效果评测（Langfuse trace / RAGAS 评测 / 脱敏 / TTL）

> 本契约定义 008 feature 涉及的内部行为与脚本契约。无新增对外 HTTP 端点（Langfuse/RAGAS 是观测/离线评测，非业务 API）。

## 1. Langfuse 全链路 trace（US2）

**接入点**: `agent_service.handle_message` 顶层 + 四段子 span。

**契约**:
- `handle_message` 被 `@observe()` 装饰（顶层 trace，name="agent.handle_message"）
- trace.metadata = {session_id, tenant_id, user_id}
- 四段 span（按执行顺序）：
  - `router`: input=user_message(脱敏), output={intent, confidence, sentiment, slots}, metadata={elapsed_ms}
  - `retrieval`: input=query(脱敏), output={result_count, top_scores}, metadata={hyde_fallback}（仅知识咨询路径）
  - `agent`: input=intent, output={current_agent, mcp_tool?}, metadata={elapsed_ms}
  - `generation`: input=context, output=answer_preview(脱敏), metadata={sources_count, total_elapsed_ms}
- 所有 input/output 上报前经 `mask_pii` 脱敏
- Langfuse 不可用/未配置 -> 降级跳过，不抛异常（FR-010）

**封装**: `app/core/langfuse_client.py` 提供 `get_langfuse()`（单例，未配置返回 None）+ `observe`（兼容降级的装饰器封装）。

## 2. RAGAS 量化评测脚本（US1）

**脚本**: `backend/scripts/ragas_eval.py`（独立运行，不入请求路径）

**输入**:
- `docs/eval_questions.md` 18 题（4 类：精确/语义/库外/混合）
- 知识库（已上传文档，Milvus+ES 索引就绪）
- tenant_id（默认 1，demo 租户）

**流程**:
```
for q in eval_questions(18):
    result = hybrid_search(q, tenant_id)       # 一次调用
    vector_contexts = [c.chunk_text for c in result.vector_results]   # 纯向量
    hybrid_contexts = [c.chunk_text for c in result.hybrid_results]   # 混合
    vector_answer = deepseek_generate(q, vector_contexts)
    hybrid_answer = deepseek_generate(q, hybrid_contexts)
    for method, contexts, answer in [(vector,...), (hybrid,...)]:
        metrics = ragas.evaluate(question=q, contexts=contexts, answer=answer, ground_truth=q.expected)
        # Faithfulness, Answer Relevancy, Context Recall
# 汇总：分类均值 + 混合 vs 纯向量对比
# 写入 docs/retrieval_comparison.md（追加 RAGAS 量化表）
```

**输出**: `docs/retrieval_comparison.md` 追加"RAGAS 量化指标"表 + 结论（混合 Context Recall - 纯向量 ≥ 15pp，SC-002）。

**降级**: per-题 LLM 评分失败 -> 该题标记 fail，继续；汇总报告含失败题数。

**复用**: `retrieval_service.hybrid_search`（不改检索层）；DeepSeek via langchain `ChatOpenAI`（base_url=deepseek）。

## 3. PII 脱敏（US3）

**接口**: `app/utils/desensitize.py` `mask_pii(text: str) -> str`

**规则**: 正则 `1[3-9]\d{9}` 整词匹配 -> 前3 + `****` + 后4。单向不可逆。

**调用点**:
- `trace_service.save_trace`: 入库前 mask query / answer_preview / slots 中的手机号
- `langfuse_client` 上报前: 同样 mask（与 MongoDB 一致）

**边界**:
- 不匹配 TK-/ORD- 编号（避免误伤）
- 脱敏失败 -> fallback 截断（前20字），不泄露原文

## 4. MongoDB TTL 90 天（US3）

**契约**: `agent_traces` collection 建 TTL 索引 `created_at` 字段，`expireAfterSeconds=7776000`（90天）。

**建索引**: `trace_service` 首次 save_trace 时 `create_index("created_at", expireAfterSeconds=...)`（幂等）。

**效果**: MongoDB 后台自动删除 created_at 早于 90 天的文档，零维护。

## 5. 降级契约（FR-010）

| 组件 | 失败场景 | 行为 |
|---|---|---|
| Langfuse | 未配置 key / 服务不可达 / 网络错 | SDK no-op + 应用层 try/except -> log warning，对话正常 |
| 脱敏 | 正则异常 | fallback 截断前20字，不抛 |
| TTL 索引 | 建索引失败 | log warning，trace 仍写入（仅不自动过期） |
| RAGAS | LLM 评分失败 | 该题 fail，继续，汇总含失败数 |

主流程（用户对话）100% 不受可观测/评测影响。