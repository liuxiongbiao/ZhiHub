"""
backend/app/services/trace_service.py

职责: Agent 执行轨迹写入 MongoDB + 查询，记录意图识别 -> 检索 -> 工具调用 -> 生成回答全流程。

核心设计:
  把 LangGraph final_state 拆成 4 个步骤（router/retrieval/agent/generation）写入 MongoDB agent_traces，
  TTL 90 天自动过期，避免 trace 无限膨胀。每次操作新建 MongoDB client，因为 save_trace 在子线程
  event loop 跑、查询在主 loop 跑，共享 client 会跨 loop 报 'Event loop is closed'。

关键技术点:
  - _build_steps 四步: router（意图路由）/ retrieval（混合检索）/ agent（工具执行）/ generation（生成回答）
  - mask_pii 脱敏: slots.phone、用户 query、answer_preview 入库前全部脱敏，防 PII 落库
  - TTL 索引: created_at + expireAfterSeconds=90天，MongoDB 自动过期清理，无需定时任务
  - 每次新建 client: 规避跨 event loop 共享 client 的 'Event loop is closed' 错误

依赖关系:
  - 上游: app/services/agent_service.py（daemon 线程调 save_trace）、app/api/v1/agent.py（查询 trace）
  - 下游: app/utils/desensitize.py（mask_pii）、motor（MongoDB async driver）、app/config.py（mongo_uri）
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.utils.desensitize import mask_pii

logger = logging.getLogger(__name__)

# MongoDB TTL：trace 90 天自动过期（008 US3），expireAfterSeconds 由 _ensure_ttl_index 建索引时使用
TRACE_TTL_SECONDS = 90 * 24 * 3600


def _get_collection():
    """获取 MongoDB agent_traces collection。

    设计决策:
      每次新建 client 而非复用单例，是因为 save_trace 在 agent_service 的 daemon 子线程里用
      asyncio.run 新建独立 event loop 跑，而 get_trace_by_session 在主 loop 跑。motor 的 client
      绑定创建时的 event loop，跨 loop 复用会报 'Event loop is closed'。每次新建 client 各自绑定
      当前 loop，规避跨 loop 问题。代价是连接开销，但 trace 写入频率低（每条消息一次），可接受。

    每次新建 client：save_trace 在子线程 event loop 跑、get_trace_by_session 在主 loop 跑，
    共享 client 会跨 loop 报 'Event loop is closed'。
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config import settings
    client = AsyncIOMotorClient(settings.mongo_uri)
    return client.zhihub.agent_traces


async def save_trace(
    session_id: int,
    tenant_id: int,
    message_id: int,
    state: dict[str, Any],
    total_elapsed_ms: int,
) -> str:
    """保存一次 Agent 执行的完整 trace 到 MongoDB。

    流程:
      1. _build_steps 从 final_state 构建 4 步轨迹
      2. 组装 document（含 session/tenant/message/intent/steps/耗时/时间）
      3. 确保 TTL 索引 -> insert_one
      4. 失败返回空串不抛（trace 是非关键数据）

    设计决策:
      失败只记 error 返回空串，不抛异常--trace 缺失不影响主对话，调用方（daemon 线程）也无需处理。
      document 同时存顶层 intent/confidence 和 steps 内的分步信息，便于按意图聚合统计和按步钻取。

    Args:
        session_id: 会话ID
        tenant_id: 租户ID
        message_id: 关联的消息ID
        state: LangGraph final_state
        total_elapsed_ms: 总耗时
    Returns:
        MongoDB document _id（失败返回空串）
    """
    steps = _build_steps(state, total_elapsed_ms)

    doc = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "message_id": message_id,
        "intent": state.get("intent", ""),
        "confidence": state.get("confidence", 0.0),
        "sentiment": state.get("sentiment", "normal"),
        "steps": steps,
        "total_elapsed_ms": total_elapsed_ms,
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }

    try:
        collection = _get_collection()
        await _ensure_ttl_index(collection)
        result = await collection.insert_one(doc)
        logger.info(f"Trace saved: {result.inserted_id}, steps={len(steps)}")
        return str(result.inserted_id)
    except Exception as e:
        # trace 写失败不影响主流程
        logger.error(f"Failed to save trace: {e}")
        return ""


async def _ensure_ttl_index(collection) -> None:
    """确保 agent_traces 有 90 天 TTL 索引（幂等，失败不阻断）。

    设计决策:
      create_index 幂等--已存在同字段索引不会报错。TTL 索引让 MongoDB 自动删除 created_at 超过
      90 天的文档，无需额外定时清理任务。失败只告警不阻断写入--索引缺失最坏是 trace 不自动过期
      （手动清理），不能因此让 trace 写入失败。
    """
    try:
        await collection.create_index("created_at", expireAfterSeconds=TRACE_TTL_SECONDS)
    except Exception as e:
        logger.warning(f"TTL index create skipped: {e}")


def _build_steps(state: dict[str, Any], total_elapsed_ms: int) -> list[dict]:
    """从 final_state 构建 4 步轨迹列表。

    流程:
      1. router: 意图/置信度/情绪/槽位（slots 脱敏）
      2. retrieval: 查询（脱敏）/结果数/Top3 分数（仅知识咨询有）
      3. agent: 执行的 Agent 类型（非 router 时）
      4. generation: 回答预览（脱敏）/引用数/总耗时

    设计决策:
      只在对应数据存在时才追加该步（如非知识咨询无 retrieval 步），避免空步骤污染 trace。
      所有可能含 PII 的字段（slots.phone、query、answer_preview）都过 mask_pii，保证 trace 库
      无原始手机号。elapsed_ms 多为 None（无分步计时），仅 generation 记总耗时--分步计时是未来优化点。
    """
    steps = []

    # Step 1: Router（意图路由）
    intent = state.get("intent", "")
    if intent:
        raw_slots = state.get("slots", {})
        # 脱敏 slots 中的手机号（phone 槽位）
        masked_slots = {
            k: mask_pii(v) if isinstance(v, str) else v for k, v in raw_slots.items()
        }
        steps.append({
            "step": "router",
            "label": "意图路由",
            "intent": intent,
            "confidence": state.get("confidence", 0.0),
            "sentiment": state.get("sentiment", "normal"),
            "slots": masked_slots,
            "elapsed_ms": None,  # 无分步计时
        })

    # Step 2: Retrieval（混合检索，仅知识咨询有检索结果）
    retrieval = state.get("retrieval_result", [])
    if retrieval:
        top_scores = [r.get("score", 0) for r in retrieval[:3]]
        # 取最后一条用户消息作为检索 query（避免取到 assistant 回复）
        _msgs = state.get("messages", [])
        raw_query = next((m.get("content", "") for m in reversed(_msgs) if m.get("role") == "user"), "")
        steps.append({
            "step": "retrieval",
            "label": "混合检索",
            "query": mask_pii(raw_query),  # 脱敏用户查询（可能含手机号）
            "result_count": len(retrieval),
            "top_scores": top_scores,
            "elapsed_ms": None,
        })

    # Step 3: Agent 执行（current_agent 非 router 时记录）
    current_agent = state.get("current_agent", "")
    if current_agent and current_agent != "router":
        steps.append({
            "step": "agent",
            "label": f"Agent: {current_agent}",
            "agent_type": current_agent,
            "elapsed_ms": None,
        })

    # Step 4: Generation（生成回答）
    final_response = state.get("final_response", "")
    if final_response:
        sources_count = len(retrieval) if retrieval else 0
        steps.append({
            "step": "generation",
            "label": "生成回答",
            "answer_preview": mask_pii(final_response[:100]),  # 脱敏回答预览（截 100 字）
            "sources_count": sources_count,
            "elapsed_ms": total_elapsed_ms,
        })

    return steps


async def get_trace_by_session(session_id: int) -> list[dict]:
    """查询会话的全部 trace 记录（按时间倒序）。

    失败返回空列表，不抛异常（查询是辅助功能，失败不应影响主流程）。
    """
    try:
        collection = _get_collection()
        cursor = collection.find({"session_id": session_id}).sort("created_at", -1)
        traces = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])  # ObjectId 转字符串便于序列化返回
            traces.append(doc)
        return traces
    except Exception as e:
        logger.error(f"Failed to fetch traces: {e}")
        return []


async def get_trace_by_message(message_id: int) -> dict | None:
    """查询某条消息的 trace 记录。失败返回 None 不抛。"""
    try:
        collection = _get_collection()
        doc = await collection.find_one({"message_id": message_id})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
    except Exception as e:
        logger.error(f"Failed to fetch trace: {e}")
        return None
