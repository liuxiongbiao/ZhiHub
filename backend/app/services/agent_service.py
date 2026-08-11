"""
backend/app/services/agent_service.py

职责: 接收用户消息，驱动 LangGraph 状态图执行 -> 收集 trace 写 MongoDB -> 触发配额计数
      -> LLM 异常兜底 -> 上下文 token 预算截断 -> 单会话轮次上限检查。
分工: 🔴 强模型独立完成（跨模块编排中枢，一步错全链路崩）
对应日程: Day 4 / 006 注入检测 / 007 轮次转人工 / 009 长期记忆

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

import logging
import time

from sqlalchemy.orm import Session as DbSession

from app.crud import session as session_crud
from app.services import session_service
from app.core.langfuse_client import observe
from app.core.ws_manager import manager
from app.utils.tokenizer import count_messages_tokens, truncate_messages

logger = logging.getLogger(__name__)

# Token 预算（与 BUILD_PLAN 对齐）
MAX_TOKEN_BUDGET = 4000  # 上下文 token 上限，超则截断历史
MAX_ROUNDS = 50  # 单会话最大轮次（轮次上限保护，防无限对话）


class AgentError(Exception):
    """Agent 处理异常基类."""


def _notify_new_session(*, tenant_id: int, session_id: int) -> None:
    """转人工（waiting_human）时向同租户在线坐席广播 new_session 事件（PRD 3.7）。

    事件契约与前端 AgentConsole（ws-client onMessage）对齐:
      {"type": "new_session", "session_id": N, "status": "waiting_human", "tenant_id": T}
    fire-and-forget（broadcast_to_tenant_sync），无在线坐席或失败不阻塞业务。
    """
    try:
        manager.broadcast_to_tenant_sync(
            tenant_id,
            {
                "type": "new_session",
                "session_id": session_id,
                "status": "waiting_human",
                "tenant_id": tenant_id,
            },
        )
    except Exception as e:  # noqa: BLE001 广播失败不影响主对话链路
        logger.warning(f"new_session broadcast failed (non-blocking): {e}")


def _check_rounds_handoff(session_id: int, final_state: dict) -> None:
    """轮次触发转人工（007 US1）。

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
        # 达阈值且 graph 未转人工 -> 触发轮次转人工
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
    # 终端用户标识（用于长期记忆按用户隔离；旧会话可能为 None，此时跳过记忆注入/抽取）
    end_user_id = getattr(s, "end_user_id", None)

    # 降级检查（T19）: 租户逾期未付账单被降级 -> 拒绝对话（PRD 3.9 限制功能），
    # 不调 LLM 不落库。此前 is_degraded 无业务调用方，降级链从未生效。
    # 降级只拦新消息，进行中会话允许走完（PRD 语义）。
    from app.services.billing_service import is_degraded
    if is_degraded(tenant_id):
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

    # 009 US2: 长期记忆注入（跨会话用户偏好）
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

    # 5. 🔴 Prompt 注入检测（US3）：用户输入进 Agent 决策前检测
    #    high -> 记录 SecurityIncident + 转人工，不进入 graph（FR-007/FR-008）
    final_state = _detect_injection(
        db, session_id=session_id, tenant_id=tenant_id, user_message=user_message,
    )

    if final_state is None:
        # 无注入 -> 正常 LangGraph 流程
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

        # 9. 🔴 驱动 LangGraph 图执行（同步 invoke）
        try:
            from app.agents.graph import get_graph

            graph = get_graph()
            final_state = graph.invoke(initial_state)
        except Exception as e:
            logger.exception(f"LangGraph execution failed: {e}")
            # 异常兜底：返回友好提示，会话可重试（不让 LLM 异常导致整条链路 500）
            final_state = {
                **initial_state,
                "final_response": "抱歉，系统处理您的请求时遇到了问题，请稍后重试。",
                "intent": "knowledge_consult",
                "confidence": 0.0,
            }

        # 🔴 轮次触发转人工（007 US1）：每轮 AI 回复后 incr 轮次，达阈值转人工
        _check_rounds_handoff(session_id, final_state)
    else:
        # 注入命中 -> 已记录安全事件，跳过 graph（final_state 含 should_interrupt=True，
        # 由后续统一流程置会话 waiting_human，不计费）
        logger.info(f"Session {session_id}: injection -> human handoff (graph skipped)")

    # 9. 提取结果
    final_response = final_state.get("final_response", "抱歉，系统暂时无法处理您的请求。")
    intent = final_state.get("intent", "knowledge_consult")
    confidence = final_state.get("confidence", 0.0)
    sentiment = final_state.get("sentiment", "normal")
    retrieval_result = final_state.get("retrieval_result", [])

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
        },
    )
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
    db.commit()

    # 013: 转人工时向同租户在线坐席广播 new_session（前端 AgentConsole 据此实时刷新队列）
    if should_interrupt:
        _notify_new_session(tenant_id=tenant_id, session_id=s.id)

    # 计费闸门：Agent 有效回复递增配额计数（转人工中断不计入），触发 80%/100% 预警
    if not should_interrupt:
        try:
            from app.services.billing_service import incr_usage_for_tenant
            incr_usage_for_tenant(db, tenant_id)
        except Exception as e:
            # 计费失败不阻断响应（宁可漏计费也不能让对话失败）
            logger.warning(f"Billing incr_usage failed (non-blocking): {e}")

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

    # 009 US2: 长期记忆抽取+保存（跨会话用户偏好，降级不阻断）
    try:
        from app.services.memory_service import extract_facts, save_memory
        facts = extract_facts(user_message)
        if facts and end_user_id:
            save_memory(tenant_id, end_user_id, facts)
    except Exception as e:
        logger.warning(f"Memory save failed (non-blocking): {e}")

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
        },
    }
