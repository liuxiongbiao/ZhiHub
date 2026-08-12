"""
backend/app/agents/nodes/router.py

职责: LangGraph 路由节点（入口）--一次 LLM 调用完成意图识别 + 槽位提取 + 情绪判断 + 置信度评分。

核心设计:
  路由节点是图的入口，用 PydanticAI Agent 一次调用产出结构化路由结果（intent/confidence/sentiment/slots）。
  011 之前用 litellm + 4 层 fallback JSON 解析（去围栏/正则提取/修复/默认），代码臃肿且易错；
  改用 PydanticAI output_type=RouterOutput(BaseModel)，SDK 层保证返回符合结构，类型安全无需手动解析。
  路由节点只负责"识别"，不做"分发"--分发是 graph.py 条件边 _route_intent 的职责（单一职责分离）。

关键技术点:
  - PydanticAI output_type=RouterOutput: LLM 返回自动校验为 BaseModel，无需手动 JSON 解析
  - run_sync: 同步调用适配 LangGraph 同步 invoke 架构
  - 降级 fallback: LLM 异常返回低置信度知识咨询意图，保证链路不中断
  - 单一职责: 只识别不分发，分发交给条件边

依赖关系:
  - 上游: app/agents/graph.py（router 作为入口节点注册）
  - 下游: app/agents/prompts/router.txt（system prompt）、pydantic_ai、app/core/langfuse_client.py（@observe）

输出: {intent: knowledge_consult|ticket_query|order_query|sensitive_action,
       confidence: float, slots: dict, sentiment: normal|unhappy|angry}
禁止: 在此处做分发决策（那是 graph.py 条件边的职责）
"""

import logging
import os
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from app.core.langfuse_client import observe

logger = logging.getLogger(__name__)

# 默认兜底意图（LLM 调用异常时使用）--低置信度知识咨询，让条件边据 confidence 转人工
FALLBACK_INTENT = "knowledge_consult"
FALLBACK_CONFIDENCE = 0.3  # 低于 0.4 阈值，会触发条件边转人工


# ─── PydanticAI 结构化输出模型 ───
class RouterOutput(BaseModel):
    """路由节点结构化输出（PydanticAI 保证 LLM 返回符合此结构）。

    intent: 意图（knowledge_consult/ticket_query/order_query/sensitive_action）
    confidence: 置信度 0~1，低于 0.4 触发转人工
    sentiment: 情绪（normal/unhappy/angry），angry 强制转人工
    slots: 槽位键值对（如 ticket_no/phone/order_no），供后续 Agent 复用
    """
    intent: str
    confidence: float
    sentiment: str
    slots: dict[str, str]


def _load_prompt() -> str:
    """加载路由 prompt 模板（system prompt，从 prompts/router.txt 读）。

    """
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "router.txt")
    try:
        with open(prompt_path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, OSError) as e:
        logger.warning(f"Router prompt file not found, using default: {e}")
        return "你是客服路由助手。根据用户消息判断意图、情绪、置信度并提取槽位。"


# ─── PydanticAI Agent（模块级单例）───
# 用 DeepSeek（OpenAI 兼容 API）；output_type 保证返回 RouterOutput，SDK 层校验结构
_router_agent: Agent[None, RouterOutput] = Agent(
    model="deepseek:deepseek-chat",
    output_type=RouterOutput,
    system_prompt=_load_prompt(),
)


def _fallback_result() -> dict[str, Any]:
    """降级兜底：默认知识咨询意图，低置信度（会触发条件边转人工）。"""
    return {
        "intent": FALLBACK_INTENT,
        "confidence": FALLBACK_CONFIDENCE,
        "slots": {},
        "sentiment": "normal",
    }


@observe(name="router")
def router_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点：路由识别。

    流程:
      1. 从 state.messages 取最后一条用户消息
      2. PydanticAI Agent run_sync 做意图识别（output_type=RouterOutput）
      3. 返回 {intent, confidence, sentiment, slots}
      4. 异常 -> 降级 fallback

    设计决策:
      run_sync 同步调用，适配 LangGraph 同步 invoke 架构（项目整体同步，见 agent_service 说明）。
      降级返回低置信度（0.3）而非抛异常，让条件边自然走转人工分支，避免路由故障导致整条链路崩。
      PydanticAI 失败的常见原因是 LLM 返回不符合 schema，SDK 内部会重试若干次仍失败才抛。

    从 state 中读取最新的用户消息，用 PydanticAI Agent 做意图识别，
    返回 {intent, confidence, sentiment, slots}。
    PydanticAI 失败时降级为 fallback。
    """
    messages: list[dict] = state.get("messages", [])
    if not messages:
        return _fallback_result()

    # 倒序找最后一条用户消息（跳过 assistant 回复）
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m["content"]
            break

    if not last_user:
        return _fallback_result()

    # 构建带历史上下文的 prompt：多轮槽位补全时（如"我的手机号是..."），
    # 单看当前消息无法判断意图，需结合上文（如"查工单"）才能正确路由。
    # 取最近 6 条消息（3 轮）的历史拼进 prompt 供 LLM 参考。
    recent = [m for m in messages[-7:] if m.get("role") in ("user", "assistant")]
    if len(recent) > 1:
        history = "\n".join(
            f"{'用户' if m['role']=='user' else '助手'}: {m['content'][:150]}"
            for m in recent[:-1]
        )
        prompt = f"对话历史（供参考上下文，判断当前消息意图）:\n{history}\n\n当前用户消息: {last_user}"
    else:
        prompt = last_user

    # PydanticAI 结构化输出（run_sync 适配 LangGraph 同步调用）
    try:
        # 013: async 端点（/session/message/stream）内 graph.invoke 在事件循环线程执行，
        # run_sync 内部 asyncio.run 会抛 "This event loop is already running" 导致 Router 降级；
        # 用 run_sync_in_thread 保证任意上下文都走成功路径。
        from app.utils.asyncio_compat import run_sync_in_thread

        result = run_sync_in_thread(_router_agent.run_sync, prompt)
        output = result.output
        logger.info(
            f"Router: intent={output.intent}, confidence={output.confidence:.2f}, "
            f"sentiment={output.sentiment}"
        )
        return {
            "intent": output.intent,
            "confidence": output.confidence,
            "sentiment": output.sentiment,
            "slots": output.slots,
        }
    except Exception as e:
        # 降级：LLM 异常返回低置信度兜底，条件边会据此转人工
        logger.error(f"Router PydanticAI failed, fallback: {e}")
        return _fallback_result()
