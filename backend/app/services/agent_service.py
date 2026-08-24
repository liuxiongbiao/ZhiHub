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


def _notify_new_session(*, tenant_id: int, session_id: int) -> None:
    """转人工（waiting_human）时向同租户在线坐席广播 new_session 事件（PRD 3.7）。

    事件契约与前端 AgentConsole（ws-client onMessage）对齐:
      {"type": "new_session", "session_id": N, "status": "waiting_human", "tenant_id": T}
    fire-and-forget（broadcast_to_tenant_sync），无在线坐席或失败不阻塞业务。

    : 同时向终端用户会话通道广播 waiting_human 事件，让 widget.js 实时展示
    "正在为您转接人工客服"（统一 type/payload envelope，与 agent_message 同通道）。
    """
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

    try:
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
        # 转人工写站内信（管理员可查）
        from app.services.notification_service import create_notification
        ndb = SessionLocal()
        try:
            create_notification(
                ndb, tenant_id=tenant_id,
                title="新会话转人工",
                content=f"会话 #{session_id} 已转人工，请在坐席工作台处理。",
                type="handoff",
            )
            # PRD §3.7：无在线坐席时向管理员告警
            if not session_service.has_online_agent(tenant_id):
                create_notification(
                    ndb, tenant_id=tenant_id,
                    title="无坐席在线",
                    content=f"会话 #{session_id} 已转人工，但当前无在线坐席，用户可留言自动建单。",
                    type="no_agent",
                )
        finally:
            ndb.close()
    except Exception as e:  # noqa: BLE001 广播失败不影响主对话链路
        logger.warning(f"new_session broadcast failed (non-blocking): {e}")


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

    # 1. 加载会话（带 tenant_id 过滤，防越权访问他租户会话）
    s = session_crud.get_by_id(db, session_id, tenant_id)
    if s is None:
        raise session_service.SessionNotFound(f"会话不存在: {session_id}")
    session_service.ensure_agent_execution_allowed(s)
    # 终端用户标识（用于长期记忆按用户隔离；旧会话可能为 None，此时跳过记忆注入/抽取）
    # 已注销租户禁止继续会话（readonly 允许进行中会话走完）
    from app.services.tenant_service import require_message_allowed_tenant
    require_message_allowed_tenant(db, tenant_id)

    # 已注销租户禁止继续会话（readonly 允许进行中会话走完）
    from app.services.tenant_service import require_message_allowed_tenant
    require_message_allowed_tenant(db, tenant_id)

    end_user_id = getattr(s, "end_user_id", None)

    # 降级检查: 租户逾期未付账单被降级 → 拒绝对话（PRD §3.9 限制功能），
    # 不调 LLM 不落库。此前 is_degraded 无业务调用方，降级链从未生效。
    # 降级只拦新消息，进行中会话允许走完（PRD 语义）。
    from app.services.billing_service import is_degraded
    try:
        degraded = is_degraded(tenant_id)
    except Exception as e:
        logger.warning(f"is_degraded check failed, assuming not degraded: {e}")
        degraded = False
    if degraded:
        # 返回结构与正常回复一致（content 为前端渲染键），metadata 带降级标记供前端提示
        return {
            "message_id": None,
            "role": "assistant",
            "content": "您的账户因账单逾期已进入降级模式，请完成支付后恢复服务",
            "sources": [],
            "metadata": {"degraded": True},
        }

    # 2. 从 Redis 加载上下文（TTL 30min，会话级历史）
    context = session_service.get_context(session_id)

    # 3. 追加本次用户消息到上下文
    context.append({"role": "user", "content": user_message})

    # 规格 009 US2: 长期记忆注入（跨会话用户偏好）
    if settings.llm_enabled:
        try:
            from app.services.memory_service import search_memory
            memories = search_memory(tenant_id, end_user_id, user_message) if end_user_id else []
            if memories:
                memory_text = "；".join(memories)
                # 记忆作为 system 消息插入上下文头部，LLM 据此个性化回答
                context.insert(0, {"role": "system", "content": f"用户历史记忆: {memory_text}"})
                logger.info(f"Session {session_id}: injected {len(memories)} memories")
        except Exception as e:
            # 记忆注入失败不阻断主流程
            logger.warning(f"Memory search failed (non-blocking): {e}")

    # 4. 保存用户消息到 MySQL
    session_crud.create_message(
        db, session_id=session_id, role="user", content=user_message
    )

    # 5.  Prompt 注入检测（US3）：用户输入进 Agent 决策前检测
    # high → 记录 SecurityIncident + 转人工，不进入 graph（FR-007/FR-008）
    final_state = _detect_injection(
        db, session_id=session_id, tenant_id=tenant_id, user_message=user_message,
    )

    if final_state is None:
        # 无注入 → 正常 LangGraph 流程
        # 6. 更新会话状态为 routing
        session_crud.update_status(db, s, "routing")
        db.commit()

        # 7. 截断检查（两维：轮次条数 + token 数）
        if len(context) > MAX_ROUNDS * 2:  # 每轮 user+assistant 各一条
            context = context[-(MAX_ROUNDS * 2):]
            logger.info(f"Session {session_id}: truncated to {MAX_ROUNDS} rounds")

        if count_messages_tokens(context) > MAX_TOKEN_BUDGET:
            # token 超预算：截断历史保留最近消息（截断而非终止，保证对话可继续）
            context = truncate_messages(context, MAX_TOKEN_BUDGET)
            logger.info(f"Session {session_id}: token truncation applied ({count_messages_tokens(context)} tokens)")

        # 8. 构建 LangGraph 初始 state
        initial_state = {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "messages": context,
            "intent": "",
            "confidence": 0.0,
            "sentiment": "normal",
            "slots": {},
            "retrieval_result": [],
            "current_agent": "router",
            "should_interrupt": False,
            "final_response": "",
        }

        # 9.  驱动 LangGraph 图执行（同步 invoke）。关闭开关时直接显式降级，
        # 不实例化模型客户端，也不靠“空 key 请求失败”来触发兜底。
        if not settings.llm_enabled:
            final_state = _llm_disabled_fallback(initial_state)
        else:
            try:
                from app.agents.graph import get_graph

                graph = get_graph()
                final_state = graph.invoke(initial_state)
            except Exception as e:
                logger.exception(f"LangGraph execution failed: {e}")
                # 异常兜底（PRD §3.4）：转人工 + 不计费，不让 LLM 异常导致整条链路 500
                final_state = {
                    **initial_state,
                    "final_response": "当前系统繁忙，已为您转接人工客服，请稍候。",
                    "intent": "knowledge_consult",
                    "confidence": 0.0,
                    "generation_failed": True,
                    "degraded": True,
                    "should_interrupt": True,
                }

        # 轮次触发转人工（规格 007 US1）：每轮 AI 回复后 incr 轮次，达阈值转人工
        _check_rounds_handoff(session_id, final_state)
    else:
        # 注入命中 → 已记录安全事件，跳过 graph（final_state 含 should_interrupt=True，
        # 由后续统一流程置会话 waiting_human，不计费）
        logger.info(f"Session {session_id}: injection -> human handoff (graph skipped)")

    # 9. 提取结果
    final_response = final_state.get("final_response", "抱歉，系统暂时无法处理您的请求。")
    intent = final_state.get("intent", "knowledge_consult")
    confidence = final_state.get("confidence", 0.0)
    sentiment = final_state.get("sentiment", "normal")
    retrieval_result = final_state.get("retrieval_result", [])

    # 计费口径（PRD §3.4/3.9）：配额在会话进入 RESOLVED 时由收尾任务统一计数，
    # 此处不再按"首条回复"即时计费；仅标记该会话是否产生有效回复（失败兜底不计入）。

    # 10. 保存 Agent 回复到 MySQL
    elapsed = int((time.perf_counter() - start) * 1000)

    # 构建 sources 列表（从 retrieval_result 提取引用，供前端展示来源）
    sources = []
    for r in retrieval_result[:5]:  # 最多存 5 条引用
        sources.append({
            "doc": r.get("doc_filename", "未知文档"),
            "chunk_index": r.get("chunk_index", 0),
            "text": (r.get("chunk_text", ""))[:200],  # 截断存储防爆库
        })

    # token 估算（用于计费/trace）
    tokens_used = count_messages_tokens(context)

    msg = session_crud.create_message(
        db,
        session_id=session_id,
        role="assistant",
        content=final_response,
        sources=sources if sources else None,
        metadata={
            "intent": intent,
            "confidence": round(confidence, 4),
            "sentiment": sentiment,
            "elapsed_ms": elapsed,
            "tokens": tokens_used,
            "quota_status": _quota_status(db, tenant_id),
            "degraded": bool(final_state.get("degraded") or final_state.get("generation_failed")),
        },
    )
    # 审计复核：监控指标接线（Agent 回复计数）
    from app.core.metrics import AGENT_MESSAGES
    AGENT_MESSAGES.labels(role="assistant").inc()
    # commit 前保存 id：expire_on_commit 会使 commit 后 msg.id 过期，访问触发懒加载，
    # 在 anyio threadpool 中与 session 连接分配并发冲突（InvalidRequestError）
    # ⚠️ 关键：先取 msg.id 再 commit，规避 anyio 线程池下懒加载与 session 连接并发的冲突
    msg_id = msg.id

    # 11. 更新上下文（追加 assistant 消息）
    context.append({"role": "assistant", "content": final_response})
    session_service.save_context(session_id, context)

    # 12. 更新会话状态 + 意图
    # 转人工：should_interrupt=True 时会话进入 waiting_human，等坐席认领；
    # 否则为 agent_handling（AI 处理中）
    should_interrupt = final_state.get("should_interrupt", False)
    session_crud.update_status(
        db, s, "waiting_human" if should_interrupt else "agent_handling"
    )
    session_crud.update_intent(
        db, s, intent=intent, confidence=confidence, sentiment=sentiment
    )
    # 有效回复标记：转人工中断、LLM/编排失败兜底回复均不计为有效回复
    if not should_interrupt and not final_state.get("generation_failed", False):
        s.effective_reply = True
    db.commit()

    # 规格 013: 转人工时向同租户在线坐席广播 new_session（前端 AgentConsole 据此实时刷新队列）
    if should_interrupt:
        _notify_new_session(tenant_id=tenant_id, session_id=s.id)

    # 13. 保存 Trace 到 MongoDB（异步 daemon 线程，失败不影响主流程）
    # 用 daemon 线程而非 await：①主流程是同步架构无 event loop 可 await；
    # ②trace 是非关键可观测数据，daemon=True 让进程退出时不强等（避免卡住关停）；
    # ③线程内 asyncio.run 新建独立 loop 跑 async save_trace
    try:
        import threading, asyncio
        from app.services.trace_service import save_trace

        def _save_trace():
            try:
                asyncio.run(save_trace(session_id, tenant_id, msg_id, final_state, elapsed))
            except Exception as te:
                logger.warning(f"Trace save failed: {te}")

        threading.Thread(target=_save_trace, daemon=True).start()
    except Exception:
        logger.warning("Trace save skipped (MongoDB may not be available)")

    logger.info(
        f"Agent: session={session_id}, intent={intent}, "
        f"confidence={confidence:.2f}, elapsed={elapsed}ms, tokens={tokens_used}"
    )

    # 长期记忆是非关键后处理，后台执行，避免外部抽取模型延迟完整回答。
    _save_memory_non_blocking(tenant_id, end_user_id, user_message)

    return {
        "message_id": msg_id,
        "role": "assistant",
        "content": final_response,
        "sources": sources,
        "metadata": {
            "intent": intent,
            "confidence": round(confidence, 4),
            "sentiment": sentiment,
            "elapsed_ms": elapsed,
            "tokens": tokens_used,
            "quota_status": _quota_status(db, tenant_id),
            "degraded": bool(final_state.get("degraded") or final_state.get("generation_failed")),
        },
    }


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

    s = session_crud.get_by_id(db, session_id, tenant_id)
    if s is None:
        raise session_service.SessionNotFound(f"会话不存在: {session_id}")
    session_service.ensure_agent_execution_allowed(s)
    end_user_id = getattr(s, "end_user_id", None)

    from app.services.billing_service import is_degraded
    try:
        degraded = is_degraded(tenant_id)
    except Exception as e:
        logger.warning(f"is_degraded check failed, assuming not degraded: {e}")
        degraded = False
    if degraded:
        return {"need_stream": False, "content": "您的账户因账单逾期已进入降级模式，请完成支付后恢复服务",
                "retrieval_result": [], "intent": "degraded", "confidence": 1.0,
                "sentiment": "normal", "should_interrupt": False, "context": [],
                "session": s, "end_user_id": end_user_id,
                "final_state": {"generation_failed": True},
                "elapsed_ms": int((time.perf_counter() - start) * 1000)}

    context = session_service.get_context(session_id)
    cache_eligible = _is_answer_cache_eligible(user_message, context)
    context.append({"role": "user", "content": user_message})

    session_crud.create_message(db, session_id=session_id, role="user", content=user_message)

    final_state = _detect_injection(db, session_id=session_id, tenant_id=tenant_id, user_message=user_message)

    if final_state is None:
        cached = _get_cached_answer(tenant_id, user_message) if cache_eligible else None
        if cached is not None:
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
                "session": s,
                "end_user_id": end_user_id,
                "final_state": {
                    "cache_hit": True,
                    "intent": cached_intent,
                    "confidence": cached_confidence,
                    "sentiment": "normal",
                    "retrieval_result": retrieval_result,
                    "should_interrupt": False,
                },
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "cache_eligible": cache_eligible,
                "user_message": user_message,
            }

        if settings.llm_enabled:
            try:
                from app.services.memory_service import search_memory

                memories = search_memory(tenant_id, end_user_id, user_message) if end_user_id else []
                if memories:
                    memory_text = "；".join(memories)
                    context.insert(0, {"role": "system", "content": f"用户历史记忆: {memory_text}"})
            except Exception as e:
                logger.warning(f"Memory search failed (non-blocking): {e}")

        session_crud.update_status(db, s, "routing")
        db.commit()
        if len(context) > MAX_ROUNDS * 2:
            context = context[-(MAX_ROUNDS * 2):]
        if count_messages_tokens(context) > MAX_TOKEN_BUDGET:
            context = truncate_messages(context, MAX_TOKEN_BUDGET)

        initial_state = {"session_id": session_id, "tenant_id": tenant_id,
            "messages": context, "intent": "", "confidence": 0.0,
            "sentiment": "normal", "slots": {}, "retrieval_result": [],
            "current_agent": "router", "should_interrupt": False,
            "skip_generation": True, "final_response": ""}

        if not settings.llm_enabled:
            final_state = _llm_disabled_fallback(initial_state)
        else:
            try:
                from app.agents.graph import get_graph
                graph = get_graph()
                final_state = graph.invoke(initial_state)
            except Exception as e:
                logger.exception(f"LangGraph execution failed: {e}")
                final_state = {**initial_state,
                    "final_response": "当前系统繁忙，已为您转接人工客服，请稍候。",
                    "intent": "knowledge_consult", "confidence": 0.0,
                    "generation_failed": True, "degraded": True, "should_interrupt": True}
        _check_rounds_handoff(session_id, final_state)
    else:
        logger.info(f"Session {session_id}: injection -> human handoff (graph skipped)")

    final_response = final_state.get("final_response", "")
    intent = final_state.get("intent", "knowledge_consult")
    confidence = final_state.get("confidence", 0.0)
    sentiment = final_state.get("sentiment", "normal")
    retrieval_result = final_state.get("retrieval_result", [])
    should_interrupt = final_state.get("should_interrupt", False)
    need_stream = not should_interrupt and bool(retrieval_result)

    return {"need_stream": need_stream, "content": final_response if not need_stream else "",
            "retrieval_result": retrieval_result, "intent": intent, "confidence": confidence,
            "sentiment": sentiment, "should_interrupt": should_interrupt, "context": context,
            "session": s, "end_user_id": end_user_id, "final_state": final_state,
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
            "cache_eligible": cache_eligible, "user_message": user_message}


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

    sources = []
    for r in retrieval_result[:5]:
        sources.append({"doc": r.get("doc_filename", "未知文档"),
                        "chunk_index": r.get("chunk_index", 0),
                        "text": (r.get("chunk_text", ""))[:200]})

    tokens_used = count_messages_tokens(context)
    msg = session_crud.create_message(db, session_id=session_id, role="assistant",
        content=content, sources=sources if sources else None,
        metadata={"intent": intent, "confidence": round(confidence, 4),
                   "sentiment": sentiment, "elapsed_ms": elapsed, "tokens": tokens_used,
                   "quota_status": _quota_status(db, tenant_id),
                   "degraded": bool(prep.get("final_state", {}).get("degraded")
                                    or prep.get("final_state", {}).get("generation_failed"))})
    from app.core.metrics import AGENT_MESSAGES
    AGENT_MESSAGES.labels(role="assistant").inc()
    msg_id = msg.id

    context.append({"role": "assistant", "content": content})
    session_service.save_context(session_id, context)
    session_crud.update_status(db, s, "waiting_human" if should_interrupt else "agent_handling")
    session_crud.update_intent(db, s, intent=intent, confidence=confidence, sentiment=sentiment)
    # ：有效回复标记（RESOLVED 收尾时据此计费）；失败兜底/降级/空回复不计入
    if content.strip() and not should_interrupt \
            and not prep.get("final_state", {}).get("generation_failed", False):
        s.effective_reply = True
    db.commit()

    final_state = prep.get("final_state", {})
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

    if should_interrupt:
        _notify_new_session(tenant_id=tenant_id, session_id=s.id)

    try:
        import threading, asyncio as _aio
        from app.services.trace_service import save_trace
        final_state["final_response"] = content
        def _save_trace():
            try:
                _aio.run(save_trace(session_id, tenant_id, msg_id, final_state, elapsed))
            except Exception as te:
                logger.warning(f"Trace save failed: {te}")
        threading.Thread(target=_save_trace, daemon=True).start()
    except Exception:
        logger.warning("Trace save skipped")

    user_msg = context[-2]["content"] if len(context) >= 2 else ""
    _save_memory_non_blocking(tenant_id, end_user_id, user_msg)

    logger.info(f"Agent stream: session={session_id}, intent={intent}, elapsed={elapsed}ms")
    return {"message_id": msg_id, "role": "assistant", "content": content,
            "sources": sources, "metadata": {"intent": intent, "confidence": round(confidence, 4),
            "sentiment": sentiment, "elapsed_ms": elapsed, "tokens": tokens_used,
            "degraded": bool(prep.get("final_state", {}).get("degraded")
                             or prep.get("final_state", {}).get("generation_failed"))}}


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
        result = await asyncio.to_thread(
            finalize_message_stream,
            db=db,
            session_id=session_id,
            tenant_id=tenant_id,
            content=content,
            prep=prep,
        )
        # 先完成消息落库，再向客户端发送文本；长期记忆抽取已转后台，不会拖慢首包。
        yield {"type": "chunk", "chunk": content}
        yield {
            "type": "meta",
            "message_id": result.get("message_id"),
            "sources": result.get("sources", []),
            "metadata": result.get("metadata", {}),
        }
        return

    from app.core.llm_client import llm_complete_stream

    messages = build_stream_messages(user_message, prep["retrieval_result"], prep["context"])
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

    full_content = ""
    while True:
        event_type, payload = await asyncio.to_thread(event_queue.get)
        if event_type == "chunk":
            yield {"type": "chunk", "chunk": str(payload)}
            continue
        if event_type == "error":
            raise payload if isinstance(payload, Exception) else RuntimeError("流式生成失败")
        full_content = str(payload)
        break

    if not full_content:
        full_content = prep["content"]
    result = await asyncio.to_thread(
        finalize_message_stream,
        db=db,
        session_id=session_id,
        tenant_id=tenant_id,
        content=full_content,
        prep=prep,
    )
    yield {
        "type": "meta",
        "message_id": result.get("message_id"),
        "sources": result.get("sources", []),
        "metadata": result.get("metadata", {}),
    }
