"""
backend/app/services/agent_service.py

职责: 接收用户消息，驱动 LangGraph 状态图执行 -> 收集 trace 写 MongoDB -> 触发配额计数
      -> LLM 异常兜底 -> 上下文 token 预算截断 -> 单会话轮次上限检查。

核心设计:
  handle_message 是整个对话系统的主编排入口，串联：加载会话+上下文 -> 记忆注入 -> 保存用户消息
  -> 注入检测短路 -> 截断 -> graph.invoke 驱动状态图 -> 轮次转人工检查 -> 保存回复 -> 计费
  -> trace 异步入库 -> 记忆抽取。全程同步架构（graph.invoke 同步），trace 用 daemon 线程异步写
  不阻塞响应。注入检测在 graph 之前短路--high 风险直接转人工，不进 LLM 决策（防注入污染 Agent）。

关键技术点:
  - 注入检测短路: _detect_injection 在 graph 前执行，high 风险跳过 graph 直接转人工
  - 同步 invoke 架构: graph.invoke 同步驱动，简单可调试；trace 用 daemon 线程异步化不阻塞响应
  - token 预算截断: 超过 MAX_TOKEN_BUDGET 截断历史，保留最近消息，截断而非终止
  - 轮次转人工: 连续 3 轮未解决或累计 15 轮转人工，知识咨询豁免 consecutive 计数
  - daemon 线程 trace: 非关键可观测数据异步写 MongoDB，daemon=True 进程退出不强等
  - expire_on_commit 规避: commit 前先取 msg.id，避免 anyio threadpool 并发懒加载冲突

依赖关系:
  - 上游: app/api/v1/agent.py（HTTP 入口调 handle_message）
  - 下游: app/agents/graph.py（状态图）、session_service/crud（会话上下文）、security_service（注入检测）、
          memory_service（记忆注入/抽取）、billing_service（计费）、trace_service（trace）
"""

import asyncio
import hashlib
import json
import logging
import queue
import re
import threading
import time
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session as DbSession

from app.crud import session as session_crud
from app.services import session_service
from app.config import settings
from app.core.langfuse_client import observe
from app.core.ws_manager import manager
from app.utils.tokenizer import count_messages_tokens, truncate_messages

logger = logging.getLogger(__name__)

# Token 预算（与 BUILD_PLAN 对齐）
MAX_TOKEN_BUDGET = 4000  # 上下文 token 上限，超则截断历史
MAX_ROUNDS = settings.max_rounds_per_session  # 单会话最大轮次（PRD 3.4: 15，可配置）
ANSWER_CACHE_TTL_SECONDS = 3600


def _normalize_answer_cache_query(message: str) -> str:
    """归一化常见知识问题，用哈希键缓存，避免 Redis 键暴露用户原文。"""
    lowered = message.strip().lower()
    return re.sub(r"[\s，。！？、,.!?；;：:（）()]+", "", lowered)


def _is_answer_cache_eligible(user_message: str, existing_context: list[dict]) -> bool:
    """仅允许无历史、无明显业务编号或敏感操作的常见知识问题进入共享缓存。"""
    normalized = _normalize_answer_cache_query(user_message)
    if existing_context or not 4 <= len(normalized) <= 80:
        return False
    if re.search(r"\d{4,}", normalized):
        return False
    sensitive_phrases = ("我要退款", "申请退款", "取消订单", "投诉", "手机号", "身份证")
    return not any(phrase in normalized for phrase in sensitive_phrases)


def _answer_cache_key(tenant_id: int, user_message: str) -> str:
    """构造租户隔离的回答缓存键。"""
    digest = hashlib.sha256(
        _normalize_answer_cache_query(user_message).encode("utf-8")
    ).hexdigest()
    return f"answer_cache:{tenant_id}:{digest}"


def _get_cached_answer(tenant_id: int, user_message: str) -> dict | None:
    """读取并校验常见问题缓存；缓存损坏时删除并降级为正常生成。"""
    from app.core.redis import get_redis

    redis = get_redis()
    key = _answer_cache_key(tenant_id, user_message)
    raw = redis.get(key)
    if not raw:
        return None
    try:
        cached = json.loads(raw)
        if not isinstance(cached.get("content"), str) or not cached["content"].strip():
            raise ValueError("回答内容为空")
        if not isinstance(cached.get("sources", []), list):
            raise ValueError("引用结构非法")
        return cached
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        redis.delete(key)
        return None


def _store_cached_answer(tenant_id: int, user_message: str, payload: dict) -> None:
    """写入带有效期的租户级常见问题缓存，失败不阻断主链路。"""
    from app.core.redis import get_redis

    try:
        get_redis().set(
            _answer_cache_key(tenant_id, user_message),
            json.dumps(payload, ensure_ascii=False),
            ex=ANSWER_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("Answer cache write failed: %s", exc)


def _save_memory_non_blocking(tenant_id: int, end_user_id: str | None, user_message: str) -> None:
    """后台抽取并保存长期记忆；非关键任务不得延迟用户收到完整回答。"""
    if not settings.llm_enabled or not end_user_id or not user_message:
        return

    def worker() -> None:
        """执行外部模型抽取与 Milvus 保存，失败仅记录告警。"""
        try:
            from app.services.memory_service import extract_facts, save_memory

            facts = extract_facts(user_message)
            if facts:
                save_memory(tenant_id, end_user_id, facts)
        except Exception as exc:
            logger.warning("Memory save failed (non-blocking): %s", exc)

    threading.Thread(target=worker, daemon=True).start()


def _quota_status(db: DbSession, tenant_id: int) -> str:
    """请求入口配额状态标记（PRD 3.4：仅标记「配额内/超额」，不扣减）。"""
    try:
        from app.services.billing_service import check_quota, get_tenant_quota

        quota = get_tenant_quota(db, tenant_id)
        info = check_quota(tenant_id, quota=quota)
        return "overage" if info["count"] >= quota else "within"
    except Exception:
        return "unknown"


class AgentError(Exception):
    """Agent 处理异常基类."""


def _llm_disabled_fallback(base_state: dict | None = None) -> dict:
    """LLM 开关关闭时的显式降级结果，避免实例化任何模型客户端。"""
    return {
        **(base_state or {}),
        "final_response": "智能问答暂不可用，已为您转接人工客服，请稍候。",
        "intent": "knowledge_consult",
        "confidence": 0.0,
        "sentiment": "normal",
        "retrieval_result": [],
        "generation_failed": True,
        "degraded": True,
        "should_interrupt": True,
    }


def _handoff_wait_estimate(tenant_id: int) -> dict:
    """估算等待时间。查询失败时返回默认值，不阻断转人工广播。"""
    from app.core.database import SessionLocal

    estimate = {"estimated_wait_minutes": 4, "queue_length": 1, "online_agents": 0}
    try:
        estimate_db = SessionLocal()
        try:
            estimate = session_service.estimate_wait_minutes(estimate_db, tenant_id)
        finally:
            estimate_db.close()
    except Exception as exc:  # 等待时间是增强信息，失败不能吞掉转人工广播
        logger.warning("wait estimate unavailable (non-blocking): %s", exc)
    return estimate


def _broadcast_handoff(tenant_id: int, session_id: int, estimate: dict) -> None:
    """把转人工事件发给租户坐席和当前终端会话。"""
    manager.broadcast_to_tenant_sync(
        tenant_id,
        {
            "type": "new_session",
            "payload": {
                "session_id": session_id,
                "status": "waiting_human",
                "tenant_id": tenant_id,
                **estimate,
            },
            # 灰度兼容旧工作台；新客户端仅消费 payload。
            "session_id": session_id,
            "status": "waiting_human",
            "tenant_id": tenant_id,
        },
    )
    manager.broadcast_to_session_sync(
        session_id,
        {
            "type": "waiting_human",
            "payload": {
                "session_id": session_id,
                "status": "waiting_human",
                **estimate,
            },
        },
    )


def _record_handoff_notifications(tenant_id: int, session_id: int) -> None:
    """写入转人工站内信；没有在线坐席时再写一条告警。"""
    from app.core.database import SessionLocal
    from app.services.notification_service import create_notification

    notification_db = SessionLocal()
    try:
        create_notification(
            notification_db, tenant_id=tenant_id,
            title="新会话转人工",
            content=f"会话 #{session_id} 已转人工，请在坐席工作台处理。",
            type="handoff",
        )
        if not session_service.has_online_agent(tenant_id):
            create_notification(
                notification_db, tenant_id=tenant_id,
                title="无坐席在线",
                content=f"会话 #{session_id} 已转人工，但当前无在线坐席，用户可留言自动建单。",
                type="no_agent",
            )
    finally:
        notification_db.close()


def _notify_new_session(*, tenant_id: int, session_id: int) -> None:
    """转人工（waiting_human）时向同租户在线坐席广播 new_session 事件（PRD 3.7）。

    事件契约与前端 AgentConsole（ws-client onMessage）对齐:
      {"type": "new_session", "session_id": N, "status": "waiting_human", "tenant_id": T}
    fire-and-forget（broadcast_to_tenant_sync），无在线坐席或失败不阻塞业务。

    : 同时向终端用户会话通道广播 waiting_human 事件，让 widget.js 实时展示
    "正在为您转接人工客服"（统一 type/payload envelope，与 agent_message 同通道）。
    """
    estimate = _handoff_wait_estimate(tenant_id)
    try:
        _broadcast_handoff(tenant_id, session_id, estimate)
        _record_handoff_notifications(tenant_id, session_id)
    except Exception as exc:  # noqa: BLE001 广播失败不影响主对话链路
        logger.warning(f"new_session broadcast failed (non-blocking): {exc}")


def _check_rounds_handoff(session_id: int, final_state: dict) -> None:
    """轮次触发转人工（规格 007 US1）。

    流程:
      1. 按 intent 递增轮次计数（incr_rounds）
      2. should_handoff_by_rounds 判断是否达转人工阈值
      3. 达阈值且 graph 未决定转人工 -> 覆盖为转人工

    设计决策:
      两条转人工规则：连续 3 轮"可解决类意图"未解决、累计 15 轮。知识咨询豁免 consecutive 计数
      （探索性多轮正常），但计入 total（总轮次仍有上限保护）。若 graph 已决定转人工
      （should_interrupt=True，如愤怒情绪/低置信度），不覆盖只记录轮次，避免双重转人工消息冲突。

    每轮 AI 回复后递增轮次计数；连续3轮可解决类意图未解决或累计15轮 -> 转人工。
    若 graph 已决定转人工（should_interrupt=True）则不覆盖，仅记录轮次。
    知识咨询不计入 consecutive（豁免探索性多轮），但计入 total。
    """
    from app.services.session_service import incr_rounds, should_handoff_by_rounds

    intent = final_state.get("intent", "knowledge_consult")
    rounds = incr_rounds(session_id, intent)
    if should_handoff_by_rounds(rounds) and not final_state.get("should_interrupt", False):
        # 达阈值且 graph 未转人工 → 触发轮次转人工
        final_state["should_interrupt"] = True
        final_state["final_response"] = "您的问题已连续多轮未解决，已为您转接人工客服，请稍候。"
        final_state["intent"] = "round_handoff"
        logger.info(
            f"Session {session_id}: round-based handoff "
            f"(consecutive={rounds['consecutive']}, total={rounds['total']})"
        )


def _detect_injection(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
    user_message: str,
) -> dict | None:
    """Prompt 注入检测（US3），命中 high 风险则短路返回转人工 state。

    流程:
      1. detect_prompt_injection 检测
      2. 非 transfer_to_human（low/medium）-> 返回 None，走正常 graph
      3. high -> record_incident 记录安全事件 -> 返回转人工 final_state（跳过 graph）

    设计决策:
      检测在 graph 之前短路，是不让注入输入进 LLM 决策--一旦进 graph，Agent 可能被注入指令操纵
      调工具/泄露信息。转人工而非拒绝服务（宪法 IV：用户体验兜底，误报可向坐席申诉）。
      record_incident 失败不阻断转人工（安全处置优先于留痕）。

    命中 high 风险 -> 记录 SecurityIncident + 返回转人工 final_state（跳过 graph）；
    未命中或仅 medium -> 返回 None，走正常 LangGraph 流程。

    转人工而非拒绝服务（宪法 IV：用户体验兜底，误报可向坐席申诉）。
    """
    from app.services.security_service import detect_prompt_injection, record_incident

    result = detect_prompt_injection(user_message)
    if result.action != "transfer_to_human":
        # low/medium：不短路，走正常 graph（medium 仅标记观察）
        return None

    # 记录安全事件供事后审查（FR-008）；写入失败不阻断转人工
    try:
        record_incident(
            db, tenant_id=tenant_id, session_id=session_id,
            user_message=user_message, rule_hit=result.rule_hit,
            risk_level=result.risk_level, action_taken="transferred_to_human",
        )
    except Exception as e:
        logger.warning(f"record_incident failed (non-blocking): {e}")

    # 返回转人工 state，调用方据此跳过 graph 直接走转人工流程
    return {
        "final_response": "检测到您的输入可能包含不安全指令，为保障账户安全，已为您转接人工客服，请稍候。",
        "intent": "prompt_injection",
        "confidence": 1.0,
        "sentiment": "normal",
        "should_interrupt": True,
        "retrieval_result": [],
    }


def _load_message_session(db: DbSession, session_id: int, tenant_id: int):
    """加载本租户会话；不存在或不允许执行时按原异常退出。"""
    session = session_crud.get_by_id(db, session_id, tenant_id)
    if session is None:
        raise session_service.SessionNotFound(f"会话不存在: {session_id}")
    session_service.ensure_agent_execution_allowed(session)
    return session


def _tenant_is_degraded(tenant_id: int) -> bool:
    """账单降级查询失败时视为未降级，避免计费依赖把对话打成 500。"""
    from app.services.billing_service import is_degraded

    try:
        return bool(is_degraded(tenant_id))
    except Exception as exc:
        logger.warning(f"is_degraded check failed, assuming not degraded: {exc}")
        return False


def _inject_long_term_memory(
    context: list,
    *,
    tenant_id: int,
    end_user_id: str | None,
    user_message: str,
    session_id: int | None = None,
) -> None:
    """把跨会话记忆插到上下文头部。失败不阻断；只有同步入口记录注入条数。"""
    if not settings.llm_enabled:
        return
    try:
        from app.services.memory_service import search_memory

        memories = search_memory(tenant_id, end_user_id, user_message) if end_user_id else []
        if not memories:
            return
        memory_text = "；".join(memories)
        context.insert(0, {"role": "system", "content": f"用户历史记忆: {memory_text}"})
        if session_id is not None:
            logger.info(f"Session {session_id}: injected {len(memories)} memories")
    except Exception as exc:
        logger.warning(f"Memory search failed (non-blocking): {exc}")


def _trim_message_context(context: list, *, session_id: int | None = None) -> list:
    """按轮次和 token 预算截断历史。传入 session_id 时保留同步入口的截断日志。"""
    if len(context) > MAX_ROUNDS * 2:
        context = context[-(MAX_ROUNDS * 2):]
        if session_id is not None:
            logger.info(f"Session {session_id}: truncated to {MAX_ROUNDS} rounds")
    if count_messages_tokens(context) > MAX_TOKEN_BUDGET:
        context = truncate_messages(context, MAX_TOKEN_BUDGET)
        if session_id is not None:
            logger.info(
                f"Session {session_id}: token truncation applied ({count_messages_tokens(context)} tokens)"
            )
    return context


def _build_graph_state(
    *,
    session_id: int,
    tenant_id: int,
    messages: list,
    skip_generation: bool = False,
) -> dict:
    """构造 LangGraph 初始状态。流式预处理额外跳过图内最终生成。"""
    state = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "messages": messages,
        "intent": "",
        "confidence": 0.0,
        "sentiment": "normal",
        "slots": {},
        "retrieval_result": [],
        "current_agent": "router",
        "should_interrupt": False,
    }
    if skip_generation:
        state["skip_generation"] = True
    state["final_response"] = ""
    return state


def _invoke_agent_graph(initial_state: dict) -> dict:
    """执行状态图。模型关闭或执行失败时返回同一条转人工兜底。"""
    if not settings.llm_enabled:
        return _llm_disabled_fallback(initial_state)
    try:
        from app.agents.graph import get_graph

        return get_graph().invoke(initial_state)
    except Exception as exc:
        logger.exception(f"LangGraph execution failed: {exc}")
        return {
            **initial_state,
            "final_response": "当前系统繁忙，已为您转接人工客服，请稍候。",
            "intent": "knowledge_consult",
            "confidence": 0.0,
            "generation_failed": True,
            "degraded": True,
            "should_interrupt": True,
        }


def _citation_sources(retrieval_result: list) -> list:
    """最多保留 5 条引用，正文截断到 200 字。"""
    return [
        {
            "doc": item.get("doc_filename", "未知文档"),
            "chunk_index": item.get("chunk_index", 0),
            "text": (item.get("chunk_text", ""))[:200],
        }
        for item in retrieval_result[:5]
    ]


def _reply_metadata(
    db: DbSession,
    tenant_id: int,
    *,
    intent: str,
    confidence: float,
    sentiment: str,
    elapsed_ms: int,
    tokens_used: int,
    degraded: bool,
) -> dict:
    """同步回复的落库元数据和返回元数据使用同一结构。"""
    return {
        "intent": intent,
        "confidence": round(confidence, 4),
        "sentiment": sentiment,
        "elapsed_ms": elapsed_ms,
        "tokens": tokens_used,
        "quota_status": _quota_status(db, tenant_id),
        "degraded": degraded,
    }


def _schedule_trace_save(
    session_id: int,
    tenant_id: int,
    msg_id: int,
    final_state: dict,
    elapsed: int,
    *,
    unavailable_message: str,
) -> None:
    """后台写入 trace。导入或线程启动失败只记日志。"""
    try:
        from app.services.trace_service import save_trace

        def _save_trace() -> None:
            try:
                asyncio.run(save_trace(session_id, tenant_id, msg_id, final_state, elapsed))
            except Exception as exc:
                logger.warning(f"Trace save failed: {exc}")

        threading.Thread(target=_save_trace, daemon=True).start()
    except Exception:
        logger.warning(unavailable_message)


def _append_user_message(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
    end_user_id,
    user_message: str,
) -> list:
    """把用户消息写入上下文、长期记忆和数据库。"""
    context = session_service.get_context(session_id)
    context.append({"role": "user", "content": user_message})
    _inject_long_term_memory(
        context,
        tenant_id=tenant_id,
        end_user_id=end_user_id,
        user_message=user_message,
        session_id=session_id,
    )
    session_crud.create_message(
        db, session_id=session_id, role="user", content=user_message
    )
    return context


def _execute_message_graph(
    db: DbSession,
    session,
    *,
    session_id: int,
    tenant_id: int,
    context: list,
    user_message: str,
):
    """注入命中时跳过状态图；否则截断上下文后调用图，并检查轮次转人工。"""
    final_state = _detect_injection(
        db, session_id=session_id, tenant_id=tenant_id, user_message=user_message,
    )
    if final_state is None:
        session_crud.update_status(db, session, "routing")
        db.commit()
        context = _trim_message_context(context, session_id=session_id)
        initial_state = _build_graph_state(
            session_id=session_id, tenant_id=tenant_id, messages=context,
        )
        final_state = _invoke_agent_graph(initial_state)
        _check_rounds_handoff(session_id, final_state)
    else:
        logger.info(f"Session {session_id}: injection -> human handoff (graph skipped)")
    return context, final_state


def _assistant_reply_facts(final_state: dict, context: list, start: float) -> dict:
    """从状态图结果取出落库所需字段。计时在这里结束。"""
    retrieval_result = final_state.get("retrieval_result", [])
    return {
        "final_response": final_state.get("final_response", "抱歉，系统暂时无法处理您的请求。"),
        "intent": final_state.get("intent", "knowledge_consult"),
        "confidence": final_state.get("confidence", 0.0),
        "sentiment": final_state.get("sentiment", "normal"),
        "sources": _citation_sources(retrieval_result),
        "tokens_used": count_messages_tokens(context),
        "degraded": bool(final_state.get("degraded") or final_state.get("generation_failed")),
        "elapsed": int((time.perf_counter() - start) * 1000),
        "should_interrupt": final_state.get("should_interrupt", False),
        "generation_failed": final_state.get("generation_failed", False),
    }


def _save_assistant_message(db: DbSession, *, session_id: int, tenant_id: int, facts: dict) -> int:
    """写入助手消息并计数。先取 id，避免提交后懒加载。"""
    stored_metadata = _reply_metadata(
        db, tenant_id,
        intent=facts["intent"], confidence=facts["confidence"], sentiment=facts["sentiment"],
        elapsed_ms=facts["elapsed"], tokens_used=facts["tokens_used"], degraded=facts["degraded"],
    )
    msg = session_crud.create_message(
        db,
        session_id=session_id,
        role="assistant",
        content=facts["final_response"],
        sources=facts["sources"] if facts["sources"] else None,
        metadata=stored_metadata,
    )
    from app.core.metrics import AGENT_MESSAGES
    AGENT_MESSAGES.labels(role="assistant").inc()
    return msg.id


def _update_session_after_reply(
    db: DbSession,
    session,
    *,
    session_id: int,
    tenant_id: int,
    context: list,
    facts: dict,
) -> None:
    """更新上下文、会话状态和有效回复标记，转人工时通知坐席。"""
    context.append({"role": "assistant", "content": facts["final_response"]})
    session_service.save_context(session_id, context)
    session_crud.update_status(
        db, session, "waiting_human" if facts["should_interrupt"] else "agent_handling"
    )
    session_crud.update_intent(
        db, session,
        intent=facts["intent"], confidence=facts["confidence"], sentiment=facts["sentiment"],
    )
    if not facts["should_interrupt"] and not facts["generation_failed"]:
        session.effective_reply = True
    db.commit()
    if facts["should_interrupt"]:
        _notify_new_session(tenant_id=tenant_id, session_id=session.id)


def _store_assistant_reply(
    db: DbSession,
    session,
    *,
    session_id: int,
    tenant_id: int,
    context: list,
    final_state: dict,
    start: float,
) -> dict:
    """落库助手回复并更新会话。返回值不含最终 metadata，调用方在记忆写入后再计算。"""
    facts = _assistant_reply_facts(final_state, context, start)
    msg_id = _save_assistant_message(
        db, session_id=session_id, tenant_id=tenant_id, facts=facts,
    )
    _update_session_after_reply(
        db, session, session_id=session_id, tenant_id=tenant_id, context=context, facts=facts,
    )
    _schedule_trace_save(
        session_id, tenant_id, msg_id, final_state, facts["elapsed"],
        unavailable_message="Trace save skipped (MongoDB may not be available)",
    )
    logger.info(
        f"Agent: session={session_id}, intent={facts['intent']}, "
        f"confidence={facts['confidence']:.2f}, elapsed={facts['elapsed']}ms, tokens={facts['tokens_used']}"
    )
    return {
        "message_id": msg_id,
        "role": "assistant",
        "content": facts["final_response"],
        "sources": facts["sources"],
        "_meta": {
            "intent": facts["intent"],
            "confidence": facts["confidence"],
            "sentiment": facts["sentiment"],
            "elapsed_ms": facts["elapsed"],
            "tokens_used": facts["tokens_used"],
            "degraded": facts["degraded"],
        },
    }


@observe(name="agent.handle_message")
def handle_message(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
    user_message: str,
) -> dict:
    """处理用户消息的主入口，驱动整个对话编排。

    流程:
      1. 加载会话 + Redis 上下文
      2. 追加用户消息 + 长期记忆注入
      3. 保存用户消息到 DB
      4. 注入检测短路（high -> 跳过 graph 转人工）
      5. 截断检查（轮次/Token 两维）
      6. 构建 state -> graph.invoke 驱动状态图
      7. 轮次转人工检查
      8. 保存 Agent 回复到 DB
      9. 更新 Redis 上下文 + 会话状态/意图
      10. 计费 + trace 异步入库 + 记忆抽取

    设计决策:
      @observe 追踪整个 handle_message 链路到 Langfuse。同步 graph.invoke 架构简单可调试，
      trace 用 daemon 线程异步写不阻塞响应。注入检测在 graph 前短路防注入污染 Agent。

    Returns:
        SendMessageResponse dict
    """
    start = time.perf_counter()

    # 加载会话（带 tenant_id 过滤，防越权访问他租户会话）。
    # 已注销租户禁止继续会话；readonly 允许进行中会话走完。
    s = _load_message_session(db, session_id, tenant_id)
    from app.services.tenant_service import require_message_allowed_tenant
    require_message_allowed_tenant(db, tenant_id)
    end_user_id = getattr(s, "end_user_id", None)

    # 降级只拦新消息，不调 LLM、不落库。进行中会话允许走完。
    if _tenant_is_degraded(tenant_id):
        return {
            "message_id": None,
            "role": "assistant",
            "content": "您的账户因账单逾期已进入降级模式，请完成支付后恢复服务",
            "sources": [],
            "metadata": {"degraded": True},
        }

    context = _append_user_message(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        end_user_id=end_user_id,
        user_message=user_message,
    )
    context, final_state = _execute_message_graph(
        db, s,
        session_id=session_id,
        tenant_id=tenant_id,
        context=context,
        user_message=user_message,
    )
    reply = _store_assistant_reply(
        db, s,
        session_id=session_id,
        tenant_id=tenant_id,
        context=context,
        final_state=final_state,
        start=start,
    )
    meta = reply.pop("_meta")
    # 长期记忆是非关键后处理，后台执行，避免外部抽取模型延迟完整回答。
    # 仍放在第二次配额读取之前，与原先返回 metadata 的顺序一致。
    _save_memory_non_blocking(tenant_id, end_user_id, user_message)
    reply["metadata"] = _reply_metadata(db, tenant_id, **meta)
    return reply


def _degraded_stream_prep(session, end_user_id, elapsed_ms: int) -> dict:
    return {
        "need_stream": False,
        "content": "您的账户因账单逾期已进入降级模式，请完成支付后恢复服务",
        "retrieval_result": [],
        "intent": "degraded",
        "confidence": 1.0,
        "sentiment": "normal",
        "should_interrupt": False,
        "context": [],
        "session": session,
        "end_user_id": end_user_id,
        "final_state": {"generation_failed": True},
        "elapsed_ms": elapsed_ms,
    }


def _cached_stream_prep(
    session,
    *,
    tenant_id: int,
    session_id: int,
    end_user_id,
    context: list,
    cached: dict,
    cache_eligible: bool,
    user_message: str,
    elapsed_ms: int,
) -> dict:
    cached_sources = cached.get("sources", [])
    retrieval_result = [
        {
            "doc_filename": source.get("doc", "未知文档"),
            "chunk_index": source.get("chunk_index", 0),
            "chunk_text": source.get("text", ""),
            "score": 1.0,
        }
        for source in cached_sources
        if isinstance(source, dict)
    ]
    cached_intent = cached.get("intent", "knowledge_consult")
    cached_confidence = float(cached.get("confidence", 0.96))
    logger.info("Answer cache hit: tenant=%s session=%s", tenant_id, session_id)
    return {
        "need_stream": False,
        "content": cached["content"],
        "retrieval_result": retrieval_result,
        "intent": cached_intent,
        "confidence": cached_confidence,
        "sentiment": "normal",
        "should_interrupt": False,
        "context": context,
        "session": session,
        "end_user_id": end_user_id,
        "final_state": {
            "cache_hit": True,
            "intent": cached_intent,
            "confidence": cached_confidence,
            "sentiment": "normal",
            "retrieval_result": retrieval_result,
            "should_interrupt": False,
        },
        "elapsed_ms": elapsed_ms,
        "cache_eligible": cache_eligible,
        "user_message": user_message,
    }


def _invoke_stream_graph(db: DbSession, session, context: list, *, session_id: int, tenant_id: int, end_user_id, user_message: str):
    """注入记忆后执行跳过生成的状态图，并检查轮次转人工。"""
    _inject_long_term_memory(
        context, tenant_id=tenant_id, end_user_id=end_user_id, user_message=user_message,
    )
    session_crud.update_status(db, session, "routing")
    db.commit()
    context = _trim_message_context(context)
    final_state = _invoke_agent_graph(_build_graph_state(
        session_id=session_id, tenant_id=tenant_id, messages=context, skip_generation=True,
    ))
    _check_rounds_handoff(session_id, final_state)
    return context, final_state


def _stream_preparation(
    *,
    session,
    context: list,
    final_state: dict,
    end_user_id,
    elapsed_ms: int,
    cache_eligible: bool,
    user_message: str,
) -> dict:
    """把状态图结果整理成流式预处理返回值。"""
    final_response = final_state.get("final_response", "")
    intent = final_state.get("intent", "knowledge_consult")
    confidence = final_state.get("confidence", 0.0)
    sentiment = final_state.get("sentiment", "normal")
    retrieval_result = final_state.get("retrieval_result", [])
    should_interrupt = final_state.get("should_interrupt", False)
    need_stream = not should_interrupt and bool(retrieval_result)
    return {
        "need_stream": need_stream,
        "content": final_response if not need_stream else "",
        "retrieval_result": retrieval_result,
        "intent": intent,
        "confidence": confidence,
        "sentiment": sentiment,
        "should_interrupt": should_interrupt,
        "context": context,
        "session": session,
        "end_user_id": end_user_id,
        "final_state": final_state,
        "elapsed_ms": elapsed_ms,
        "cache_eligible": cache_eligible,
        "user_message": user_message,
    }


def _append_stream_user(db: DbSession, session_id: int, user_message: str):
    """写入本轮用户消息，并判断这条消息能否进入回答缓存。"""
    context = session_service.get_context(session_id)
    cache_eligible = _is_answer_cache_eligible(user_message, context)
    context.append({"role": "user", "content": user_message})
    session_crud.create_message(db, session_id=session_id, role="user", content=user_message)
    return context, cache_eligible


def prepare_message_stream(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
    user_message: str,
) -> dict:
    """流式消息预处理: 执行 graph.invoke 获取路由+检索结果，但不生成最终回答。

    与 handle_message 的区别: graph.invoke 完成路由+检索后，提取 retrieval_result，
    由调用方通过 llm_complete_stream 流式生成回答（token 级真流式）。
    """
    start = time.perf_counter()

    s = _load_message_session(db, session_id, tenant_id)
    end_user_id = getattr(s, "end_user_id", None)

    if _tenant_is_degraded(tenant_id):
        return _degraded_stream_prep(
            s, end_user_id, int((time.perf_counter() - start) * 1000),
        )

    context, cache_eligible = _append_stream_user(db, session_id, user_message)

    final_state = _detect_injection(db, session_id=session_id, tenant_id=tenant_id, user_message=user_message)

    if final_state is None:
        cached = _get_cached_answer(tenant_id, user_message) if cache_eligible else None
        if cached is not None:
            return _cached_stream_prep(
                s,
                tenant_id=tenant_id,
                session_id=session_id,
                end_user_id=end_user_id,
                context=context,
                cached=cached,
                cache_eligible=cache_eligible,
                user_message=user_message,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )

        context, final_state = _invoke_stream_graph(
            db, s, context,
            session_id=session_id, tenant_id=tenant_id, end_user_id=end_user_id, user_message=user_message,
        )
    else:
        logger.info(f"Session {session_id}: injection -> human handoff (graph skipped)")

    return _stream_preparation(
        session=s,
        context=context,
        final_state=final_state,
        end_user_id=end_user_id,
        elapsed_ms=int((time.perf_counter() - start) * 1000),
        cache_eligible=cache_eligible,
        user_message=user_message,
    )


def _persist_streamed_reply(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
    content: str,
    prep: dict,
    session,
    context: list,
    intent: str,
    confidence: float,
    sentiment: str,
    should_interrupt: bool,
    elapsed: int,
    sources: list,
) -> tuple[int, int]:
    """保存助手回复、上下文和会话状态，并提交。"""
    tokens_used = count_messages_tokens(context)
    message = session_crud.create_message(
        db, session_id=session_id, role="assistant",
        content=content, sources=sources if sources else None,
        metadata={"intent": intent, "confidence": round(confidence, 4),
                  "sentiment": sentiment, "elapsed_ms": elapsed, "tokens": tokens_used,
                  "quota_status": _quota_status(db, tenant_id),
                  "degraded": bool(prep.get("final_state", {}).get("degraded")
                                   or prep.get("final_state", {}).get("generation_failed"))},
    )
    from app.core.metrics import AGENT_MESSAGES
    AGENT_MESSAGES.labels(role="assistant").inc()

    context.append({"role": "assistant", "content": content})
    session_service.save_context(session_id, context)
    session_crud.update_status(db, session, "waiting_human" if should_interrupt else "agent_handling")
    session_crud.update_intent(db, session, intent=intent, confidence=confidence, sentiment=sentiment)
    # ：有效回复标记（RESOLVED 收尾时据此计费）；失败兜底/降级/空回复不计入
    if content.strip() and not should_interrupt \
            and not prep.get("final_state", {}).get("generation_failed", False):
        session.effective_reply = True
    db.commit()
    return message.id, tokens_used


def _remember_stream_answer(
    tenant_id: int,
    prep: dict,
    final_state: dict,
    *,
    content: str,
    intent: str,
    confidence: float,
    should_interrupt: bool,
    sources: list,
) -> None:
    """知识问答成功且带引用时写入回答缓存。"""
    if (
        prep.get("cache_eligible")
        and not final_state.get("cache_hit")
        and intent == "knowledge_consult"
        and not should_interrupt
        and not final_state.get("generation_failed")
        and content.strip()
        and sources
    ):
        _store_cached_answer(
            tenant_id,
            prep.get("user_message", ""),
            {
                "content": content,
                "sources": sources,
                "intent": intent,
                "confidence": round(confidence, 4),
            },
        )


def _stream_reply_view(content: str, sources: list, intent: str, confidence: float, sentiment: str, elapsed: int, tokens_used: int, prep: dict) -> dict:
    final_state = prep.get("final_state", {})
    return {
        "message_id": None,
        "role": "assistant",
        "content": content,
        "sources": sources,
        "metadata": {
            "intent": intent,
            "confidence": round(confidence, 4),
            "sentiment": sentiment,
            "elapsed_ms": elapsed,
            "tokens": tokens_used,
            "degraded": bool(final_state.get("degraded") or final_state.get("generation_failed")),
        },
    }


def finalize_message_stream(db: DbSession, *, session_id: int, tenant_id: int,
                            content: str, prep: dict) -> dict:
    """流式消息后处理: 保存 assistant 消息 + 计费 + trace + 记忆抽取。"""
    s = prep["session"]
    context = prep["context"]
    intent = prep["intent"]
    confidence = prep["confidence"]
    sentiment = prep["sentiment"]
    retrieval_result = prep["retrieval_result"]
    should_interrupt = prep["should_interrupt"]
    end_user_id = prep["end_user_id"]
    elapsed = prep["elapsed_ms"]

    sources = _citation_sources(retrieval_result)
    msg_id, tokens_used = _persist_streamed_reply(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        content=content,
        prep=prep,
        session=s,
        context=context,
        intent=intent,
        confidence=confidence,
        sentiment=sentiment,
        should_interrupt=should_interrupt,
        elapsed=elapsed,
        sources=sources,
    )

    final_state = prep.get("final_state", {})
    _remember_stream_answer(
        tenant_id, prep, final_state,
        content=content, intent=intent, confidence=confidence,
        should_interrupt=should_interrupt, sources=sources,
    )

    if should_interrupt:
        _notify_new_session(tenant_id=tenant_id, session_id=s.id)

    final_state["final_response"] = content
    _schedule_trace_save(
        session_id, tenant_id, msg_id, final_state, elapsed,
        unavailable_message="Trace save skipped",
    )

    user_msg = context[-2]["content"] if len(context) >= 2 else ""
    _save_memory_non_blocking(tenant_id, end_user_id, user_msg)
    logger.info(f"Agent stream: session={session_id}, intent={intent}, elapsed={elapsed}ms")
    reply = _stream_reply_view(
        content, sources, intent, confidence, sentiment, elapsed, tokens_used, prep,
    )
    reply["message_id"] = msg_id
    return reply


def build_stream_messages(user_message: str, retrieval_result: list, context: list) -> list:
    """构建流式 LLM 调用的 messages（复用 knowledge prompt 模板）。"""
    from app.agents.nodes.knowledge_agent import _build_sources_text, _load_prompt
    sources_text = _build_sources_text(retrieval_result)
    template = _load_prompt()
    prompt = template.replace("{sources}", sources_text).replace("{query}", user_message)
    messages = [{"role": "system", "content": prompt}]
    history = context[-7:-1] if len(context) > 1 else []
    for h in history:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages


async def _iter_llm_chunks(messages: list, captured: dict) -> AsyncIterator[str]:
    """在后台线程消费同步模型流，按到达顺序产出文本片段。失败时抛出原始异常。"""
    from app.core.llm_client import llm_complete_stream

    event_queue: queue.Queue[tuple[str, object]] = queue.Queue()

    def _stream_worker() -> None:
        """在独立线程中消费同步模型流，并把结果投递给异步生成器。"""
        full: list[str] = []
        try:
            for chunk in llm_complete_stream(messages=messages, max_tokens=800, temperature=0.3):
                full.append(chunk)
                event_queue.put(("chunk", chunk))
            event_queue.put(("complete", "".join(full)))
        except Exception as exc:  # pragma: no cover - 由端点契约测试覆盖异常事件
            event_queue.put(("error", exc))

    threading.Thread(target=_stream_worker, daemon=True).start()
    while True:
        event_type, payload = await asyncio.to_thread(event_queue.get)
        if event_type == "chunk":
            yield str(payload)
            continue
        if event_type == "error":
            raise payload if isinstance(payload, Exception) else RuntimeError("流式生成失败")
        captured["full"] = str(payload)
        return


def _stream_meta_event(result: dict) -> dict:
    """把已落库的回复收成流式通道的 meta 事件。"""
    return {
        "type": "meta",
        "message_id": result.get("message_id"),
        "sources": result.get("sources", []),
        "metadata": result.get("metadata", {}),
    }


async def _finalize_stream_event(
    db: DbSession, *, session_id: int, tenant_id: int, content: str, prep: dict,
) -> dict:
    result = await asyncio.to_thread(
        finalize_message_stream,
        db=db,
        session_id=session_id,
        tenant_id=tenant_id,
        content=content,
        prep=prep,
    )
    return _stream_meta_event(result)


async def stream_message_events(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
    user_message: str,
) -> AsyncIterator[dict]:
    """统一生成会话流式领域事件，供管理端和 Widget 两个 HTTP 通道复用。

    事件只有两类：``chunk`` 表示增量文本，``meta`` 表示已落库的消息编号、引用和
    元数据。同步 LLM 迭代器通过后台线程与队列桥接，避免阻塞 FastAPI 事件循环。
    """
    prep = await asyncio.to_thread(
        prepare_message_stream,
        db=db,
        session_id=session_id,
        tenant_id=tenant_id,
        user_message=user_message,
    )

    if not prep["need_stream"]:
        content = prep["content"]
        # 先完成消息落库，再向客户端发送文本；长期记忆抽取已转后台，不会拖慢首包。
        yield {"type": "chunk", "chunk": content}
        yield await _finalize_stream_event(
            db, session_id=session_id, tenant_id=tenant_id, content=content, prep=prep,
        )
        return

    messages = build_stream_messages(user_message, prep["retrieval_result"], prep["context"])
    captured: dict = {}
    async for chunk in _iter_llm_chunks(messages, captured):
        yield {"type": "chunk", "chunk": chunk}

    full_content = captured.get("full", "") or prep["content"]
    yield await _finalize_stream_event(
        db, session_id=session_id, tenant_id=tenant_id, content=full_content, prep=prep,
    )
