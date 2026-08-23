"""
backend/app/agents/nodes/router.py

职责: LangGraph 路由节点（入口）--一次 LLM 调用完成意图识别 + 槽位提取 + 情绪判断 + 置信度评分。

核心设计:
  路由节点是图的入口，用 LangChain 结构化输出一次调用产出路由结果（intent/confidence/sentiment/slots）。
  早期用 litellm + 4 层 fallback JSON 解析（去围栏/正则提取/修复/默认），代码臃肿且易错；
  现改用 LangChain with_structured_output
  （ChatOpenAI 走 DeepSeek OpenAI 兼容接口 + function calling），SDK 层保证返回符合结构，类型安全。
  路由节点只负责"识别"，不做"分发"--分发是 graph.py 条件边 _route_intent 的职责（单一职责分离）。

关键技术点:
  - LangChain with_structured_output: LLM 返回自动校验为 BaseModel，无需手动 JSON 解析
  - invoke: 同步调用适配 LangGraph 同步 invoke 架构
  - 降级 fallback: LLM 异常返回低置信度知识咨询意图，保证链路不中断
  - 单一职责: 只识别不分发，分发交给条件边

依赖关系:
  - 上游: app/agents/graph.py（router 作为入口节点注册）
  - 下游: app/agents/prompts/router.txt（system prompt）、langchain_openai、app/core/langfuse_client.py（@observe）

输出: {intent: knowledge_consult|ticket_query|order_query|sensitive_action,
       confidence: float, slots: dict, sentiment: normal|unhappy|angry}
禁止: 在此处做分发决策（那是 graph.py 条件边的职责）
"""

import logging
import os
import re
from typing import Any

from pydantic import BaseModel
from langchain_openai import ChatOpenAI

from app.core.langfuse_client import observe

logger = logging.getLogger(__name__)

# 默认兜底意图（LLM 调用异常时使用）——低置信度知识咨询，让条件边据 confidence 转人工
FALLBACK_INTENT = "knowledge_consult"
FALLBACK_CONFIDENCE = 0.3  # 低于 0.4 阈值，会触发条件边转人工


# ─── 路由结构化输出模型 ───
class RouterOutput(BaseModel):
    """路由节点结构化输出（LangChain with_structured_output 校验 LLM 返回符合此结构）。

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


# ─── LangChain 结构化输出链（模块级惰性单例）───
# 用 DeepSeek 的 OpenAI 兼容接口（ChatOpenAI + base_url），with_structured_output
# 通过 function calling 保证返回 RouterOutput，SDK 层校验结构。
# 惰性构建（而非 import 期构建）：settings 的 API Key 由 .env 注入，
# 且避免测试/无 LLM 环境下 import 即初始化模型客户端。
_router_agent = None  # ChatOpenAI.with_structured_output(RouterOutput) 实例


def _get_router_agent():
    """获取/构建 Router 结构化输出链（惰性单例）。"""
    global _router_agent
    if _router_agent is None:
        from app.config import settings

        # litellm 风格模型名 "deepseek/deepseek-chat" → LangChain 的 "deepseek-chat"
        model_name = settings.llm_primary_model.split("/", 1)[-1]
        llm = ChatOpenAI(
            model=model_name,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=0.0,   # 路由要确定性：与查询改写一致用 0
            timeout=60,        # 与 llm_client.LLM_TIMEOUT_SECONDS 对齐
            max_retries=1,     # 失败快速兜底，不拖慢请求
        )
        # DeepSeek 不支持 OpenAI 的 response_format=json_schema 严格模式（实测 400:
        # "This response_format type is unavailable now"），显式用 function_calling
        # （tools 机制）做结构化输出，DeepSeek 完全兼容。
        _router_agent = llm.with_structured_output(RouterOutput, method="function_calling")
    return _router_agent


def _fallback_result() -> dict[str, Any]:
    """降级兜底：默认知识咨询意图，低置信度（会触发条件边转人工）。"""
    return {
        "intent": FALLBACK_INTENT,
        "confidence": FALLBACK_CONFIDENCE,
        "slots": {},
        "sentiment": "normal",
    }


def _extract_fast_slots(message: str) -> dict[str, str]:
    """从单轮明确问题中提取常见业务槽位，供流式快速路由复用。"""
    slots: dict[str, str] = {}
    patterns = {
        "phone": r"(?<!\d)(1[3-9]\d{9})(?!\d)",
        "ticket_no": r"(?i)(?:工单号|ticket)\s*[:：#]?\s*([A-Z0-9-]{4,32})",
        "order_no": r"(?i)(?:订单号|order)\s*[:：#]?\s*([A-Z0-9-]{4,32})",
    }
    for key, pattern in patterns.items():
        matched = re.search(pattern, message)
        if matched:
            slots[key] = matched.group(1)
    return slots


def _fast_stream_route(message: str, *, has_history: bool) -> dict[str, Any] | None:
    """为首轮、意图明确的流式请求返回确定性结构化路由。

    流式链路最关心首包时间；首轮消息没有上下文消歧需求时，无需为「知识咨询」
    再等待一次远端路由模型。涉及多轮上下文或内容过短时仍回退到 LLM，避免牺牲准确率。
    """
    text = message.strip()
    if has_history or len(text) < 2:
        return None

    slots = _extract_fast_slots(text)
    angry_words = ("投诉", "骗子", "垃圾", "太差", "气死", "愤怒")
    if any(word in text for word in angry_words):
        return {
            "intent": "knowledge_consult",
            "confidence": 0.99,
            "sentiment": "angry",
            "slots": slots,
        }

    # 政策/流程类问题仍属于知识咨询；明确要求执行退款、退货或取消才是敏感操作。
    sensitive_patterns = (
        r"(?:我要|帮我|申请|立即|现在).{0,4}(?:退款|退货|取消订单)",
        r"(?:退款|退货|取消订单).{0,4}(?:申请|操作|办理)",
    )
    if any(re.search(pattern, text) for pattern in sensitive_patterns):
        intent = "sensitive_action"
    elif "ticket_no" in slots or "工单" in text:
        intent = "ticket_query"
    elif "order_no" in slots or any(word in text for word in ("订单", "物流", "快递", "发货", "收货")):
        intent = "order_query"
    else:
        intent = "knowledge_consult"

    return {
        "intent": intent,
        "confidence": 0.96,
        "sentiment": "normal",
        "slots": slots,
    }


@observe(name="router")
def router_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点：路由识别。

    流程:
      1. 从 state.messages 取最后一条用户消息
      2. LangChain 结构化输出链 invoke 做意图识别（with_structured_output=RouterOutput）
      3. 返回 {intent, confidence, sentiment, slots}
      4. 异常 -> 降级 fallback

    设计决策:
      invoke 同步调用，适配 LangGraph 同步 invoke 架构（项目整体同步，见 agent_service 说明）。
      降级返回低置信度（0.3）而非抛异常，让条件边自然走转人工分支，避免路由故障导致整条链路崩。
      结构化输出失败的常见原因是 LLM 返回不符合 schema，SDK 内部会重试（max_retries=1）仍失败才抛。
      为兼容同步和异步上下文，通过 run_sync_in_thread() 调用（事件循环线程中会转到独立线程执行）。
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
    if state.get("skip_generation"):
        fast_result = _fast_stream_route(last_user, has_history=len(recent) > 1)
        if fast_result is not None:
            logger.info(
                "Router fast path: intent=%s, confidence=%.2f",
                fast_result["intent"],
                fast_result["confidence"],
            )
            return fast_result
    if len(recent) > 1:
        history = "\n".join(
            f"{'用户' if m['role']=='user' else '助手'}: {m['content'][:150]}"
            for m in recent[:-1]
        )
        prompt = f"对话历史（供参考上下文，判断当前消息意图）:\n{history}\n\n当前用户消息: {last_user}"
    else:
        prompt = last_user

    # LangChain 结构化输出（invoke 适配 LangGraph 同步调用）
    try:
        # 规格 013: async 端点（/session/message/stream）内 graph.invoke 在事件循环线程执行，
        # 用 run_sync_in_thread 保证任意上下文都走成功路径（无 loop 时直接调用，零开销）。
        from app.utils.asyncio_compat import run_sync_in_thread

        # system prompt（router.txt 的意图/情绪/槽位定义）+ 运行时 prompt（历史 + 当前消息）
        messages = [
            {"role": "system", "content": _load_prompt()},
            {"role": "user", "content": prompt},
        ]
        output = run_sync_in_thread(_get_router_agent().invoke, messages)
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
        logger.error(f"Router structured output failed, fallback: {e}")
        return _fallback_result()
