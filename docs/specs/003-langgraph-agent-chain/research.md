# Research: LangGraph 智能体主链路骨架

**Phase 0 Output** | **Date**: 2026-08-04

## R1: LangGraph State 设计

**Decision**: 使用 TypedDict 定义 AgentState，字段与 BUILD_PLAN 对齐。

```python
class AgentState(TypedDict):
    session_id: int
    tenant_id: int
    messages: list[dict]             # [{"role":"user","content":"..."}, ...]
    intent: str                      # knowledge_consult | ticket_query | order_query | sensitive_action
    confidence: float                # 0.0-1.0
    sentiment: str                   # normal | unhappy | angry
    slots: dict                      # {"ticket_id": None, "order_id": None, ...}
    retrieval_result: list[dict]     # 混合检索返回的片段列表
    current_agent: str               # 当前处理节点名
    should_interrupt: bool           # 是否触发人工中断
    final_response: str              # 最终回复
```

**Rationale**: TypedDict 是 LangGraph 1.0+ 推荐的状态定义方式，比 Pydantic BaseModel 更轻量，与 StateGraph 的 reducer 机制兼容。

## R2: 图拓扑设计

**Decision**: 本日只实现 router → knowledge_agent 主分支，条件边路由到占位节点。

```
router ──┬── intent=knowledge_consult ──→ knowledge_agent ──→ END
          ├── intent=ticket_query ──→ ticket_agent(skeleton) ──→ END
          ├── intent=order_query ──→ order_agent(skeleton) ──→ END
          └── sentiment=angry OR intent=sensitive_action ──→ human_handoff(skeleton) ──→ END
```

**Rationale**: Day 5-6 将逐步替换 skeleton 节点为真实实现。条件边逻辑集中在 graph.py 的 `route_intent()` 函数中。

## R3: LLM 调用策略

**Decision**: 通过 litellm 统一调用 DeepSeek，路由节点用 temperature=0.0（确保意图识别稳定），知识 Agent 用 temperature=0.3。

**Rationale**: temperature=0 消除随机性适合分类任务，0.3 给生成任务保留一定灵活度同时控制幻觉。

## R4: Session 状态机（Day 4 简化版）

**Decision**: Day 4 实现 4 态：CREATED → ROUTING → AGENT_HANDLING → RESOLVED。WAITING_HUMAN / HUMAN_HANDLING 留到 Day 6。

```
CREATED ──→ ROUTING ──→ AGENT_HANDLING ──→ RESOLVED ──→ CLOSED
```

**Rationale**: Day 4 只跑知识问答链路，不涉及人工介入，4 态足够覆盖"Widget发送→Agent处理→完成"的全流程。

## R5: Redis 会话上下文设计

**Decision**: 键 `session:context:{session_id}`，值 JSON 序列化最近 10 轮消息，TTL=30min（与空闲超时一致）。

```json
{"messages": [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}], "updated_at": 1712345678}
```

**Rationale**: 每次 Agent 处理时从 Redis 读取上下文（比每次查 MySQL 快），处理完后写回并刷新 TTL。30min TTL 与空闲超时一致，自动过期无需清理任务。

## R6: Token 截断策略

**Decision**: 按字符数估算 token（中文 ~1.5 chars/token，英文 ~4 chars/token），超过 4000 token 预算时从最早的消息开始丢弃，保留最近 10 轮。

```python
def estimate_tokens(text: str) -> int:
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)
```

**Rationale**: 生产级项目会用 tiktoken，但 demo 阶段不需要引入 PyTorch/tiktoken 重依赖。字符数估算误差在 ±20% 内，对截断决策影响不大。

## R7: Prompt 模板策略

**Decision**: router.txt 包含 few-shot 示例（至少 4 条覆盖 4 种意图），knowledge.txt 要求强制引用来源 + 防幻觉指令。

**Rationale**: few-shot 是提升小模型意图识别准确率最直接有效的方法。防幻觉指令（"如果片段不足明确告知不编造"）是 RAG 应用的基本要求。
