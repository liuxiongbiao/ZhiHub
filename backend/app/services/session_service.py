"""
backend/app/services/session_service.py

职责: 会话生命周期管理--创建、状态流转、Redis 上下文读写、轮次计数转人工、在线坐席 presence。

核心设计:
  四块功能：①Redis 上下文读写（最近 N 轮消息，TTL 30min 空闲超时）；②轮次计数（consecutive/total/last_intent），
  连续 3 轮可解决类意图未解决或累计 15 轮转人工；③在线坐席 presence（客服独立 TTL key，判断有无坐席决定留言建单）；
  ④会话创建与详情。PRD V1.6 起知识咨询也计入连续相同意图轮次，防止重复追问陷入循环。

关键技术点:
  - Redis 上下文: 最近 10 轮消息，TTL 30min，滚动续期
  - 轮次计数三态: consecutive（连续同意图）/ total（累计）/ last_intent（判 streak）
  - presence TTL key: WebSocket 建连/心跳续期，断开删除；进程异常时由 Redis 自动过期
  - 转人工阈值: consecutive>=3 或 total>=15

依赖关系:
  - 上游: app/services/agent_service.py（上下文/轮次）、auth_service.py（presence）、app/api/v1/session.py
  - 下游: app/core/redis.py、app/crud/session.py、app/models/session.py
"""

import json
import logging
import re
import time

from sqlalchemy.orm import Session as DbSession

from app.core.redis import get_redis
from app.crud import answer_feedback as feedback_crud
from app.crud import csat as csat_crud
from app.crud import quick_reply as quick_reply_crud
from app.crud import session as session_crud
from app.models.session import Session

logger = logging.getLogger(__name__)

_ORDER_NO_RE = re.compile(r"\bORD-[A-Za-z0-9-]{3,26}\b", re.IGNORECASE)
_TICKET_NO_RE = re.compile(r"\bTK-[A-Za-z0-9-]{3,26}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_LAST4_RE = re.compile(r"(?:后四位|尾号)\s*[:：]?\s*(\d{4})")

# Redis 上下文键前缀 + TTL
CONTEXT_KEY_PREFIX = "session:context:"
CONTEXT_TTL_SECONDS = 30 * 60  # 30 分钟空闲超时
MAX_CONTEXT_MESSAGES = 10  # 最多保留最近 10 轮


# ─── 会话上下文（Redis）───

def _context_key(session_id: int) -> str:
    """拼装会话上下文 Redis key。"""
    return f"{CONTEXT_KEY_PREFIX}{session_id}"


def get_context(session_id: int) -> list[dict]:
    """从 Redis 读取会话上下文（最近 N 轮消息）。无记录返回空列表。"""
    r = get_redis()
    data = r.get(_context_key(session_id))
    if data:
        try:
            return json.loads(data).get("messages", [])
        except (json.JSONDecodeError, TypeError):
            # 缓存数据损坏（异常写入/版本升级）时清掉重来，不让主对话链路 500
            logger.warning(f"Session context corrupted, resetting: {session_id}")
            r.delete(_context_key(session_id))
    return []


def save_context(session_id: int, messages: list[dict]) -> None:
    """将会话上下文写入 Redis（自动裁剪到最近 N 轮，TTL 滚动续期）。

    设计决策:
      裁剪到最近 10 轮控制 Redis 存储与 LLM 上下文长度。每次写都重置 TTL，
      活跃会话上下文不会过期，空闲 30min 才过期触发清理。
    """
    r = get_redis()
    recent = messages[-MAX_CONTEXT_MESSAGES:] if len(messages) > MAX_CONTEXT_MESSAGES else messages
    payload = json.dumps({"messages": recent, "updated_at": int(time.time())})
    r.set(_context_key(session_id), payload, ex=CONTEXT_TTL_SECONDS)


def clear_context(session_id: int) -> None:
    """清除 Redis 中的会话上下文."""
    r = get_redis()
    r.delete(_context_key(session_id))


# ─── 会话轮次计数（规格 007 US1 轮次触发转人工）───

# 可解决类意图：连续相同意图未解决达阈值即转人工（PRD §3.4 判定口径：
# 以“连续相同意图”近似“用户重复追问同一问题”，不再豁免知识咨询）
RESOLVABLE_INTENTS = {"knowledge_consult", "ticket_query", "order_query", "sensitive_action"}
ROUND_CONSECUTIVE_THRESHOLD = 3   # 连续未解决轮次阈值
ROUND_TOTAL_THRESHOLD = 15        # 累计总轮次阈值
ROUNDS_KEY_PREFIX = "session:rounds:"
ROUNDS_TTL_SECONDS = 30 * 60      # 与空闲超时同窗，会话活跃时滚动续期


def _rounds_key(session_id: int) -> str:
    return f"{ROUNDS_KEY_PREFIX}{session_id}"


def get_rounds(session_id: int) -> dict:
    """读取会话轮次计数 {total, consecutive, last_intent}。无记录返回初始态。"""
    r = get_redis()
    data = r.get(_rounds_key(session_id))
    if data:
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"total": 0, "consecutive": 0, "last_intent": None}


def _save_rounds(session_id: int, rounds: dict) -> None:
    r = get_redis()
    r.set(_rounds_key(session_id), json.dumps(rounds), ex=ROUNDS_TTL_SECONDS)


def incr_rounds(session_id: int, intent: str) -> dict:
    """递增会话轮次计数，返回更新后的计数 dict。

    流程:
      1. 可解决类意图 + 与上轮相同 -> consecutive++（streak 累加）
      2. 可解决类意图 + 与上轮不同 -> consecutive=1（新 streak）
      3. 非可解决意图 -> consecutive=0
      4. total 始终 +1，记录 last_intent

    设计决策:
      "连续3轮相同"= consecutive 达 3（第3轮触发，无需第4轮），包含知识咨询。

    可解决类意图：与上一轮相同 -> consecutive++（streak 累加）；不同 -> 新 streak 从 1 起。
    非可解决意图 -> consecutive=0。
    total 始终 +1。返回更新后的计数 dict。
    "连续3轮相同"= consecutive 达 3（第3轮触发，无需第4轮）。
    """
    rounds = get_rounds(session_id)
    if intent in RESOLVABLE_INTENTS:
        if intent == rounds.get("last_intent"):
            rounds["consecutive"] = rounds.get("consecutive", 0) + 1
        else:
            rounds["consecutive"] = 1  # 新可解决意图 streak 从 1 起
    else:
        rounds["consecutive"] = 0  # 非可解决意图不累计连续轮次
    rounds["total"] = rounds.get("total", 0) + 1
    rounds["last_intent"] = intent
    _save_rounds(session_id, rounds)
    return rounds


def reset_consecutive(session_id: int) -> None:
    """坐席退回 AI 时重置连续未解决计数（total 保留）。

    设计决策:
      坐席处理后退回 AI，consecutive 清零给 AI 重新尝试的机会；total 保留，
      防止反复退回 AI 导致无限循环（total 达 15 仍会转人工）。
    """
    rounds = get_rounds(session_id)
    rounds["consecutive"] = 0
    _save_rounds(session_id, rounds)


def should_handoff_by_rounds(rounds: dict) -> bool:
    """是否达到轮次转人工阈值：consecutive>=3 或 total>=15。"""
    return (
        rounds.get("consecutive", 0) >= ROUND_CONSECUTIVE_THRESHOLD
        or rounds.get("total", 0) >= ROUND_TOTAL_THRESHOLD
    )


# ─── 在线坐席 presence（规格 007 US3 留言建单触发判定）───

ONLINE_AGENTS_PREFIX = "agents:online:"
AGENT_PRESENCE_TTL_SECONDS = 90


def _online_key(tenant_id: int, user_id: int) -> str:
    return f"{ONLINE_AGENTS_PREFIX}{tenant_id}:{user_id}"


def mark_agent_online(tenant_id: int, user_id: int) -> None:
    """坐席登录、WS 建连或心跳 -> 刷新独立 presence key 的 TTL。"""
    get_redis().set(
        _online_key(tenant_id, user_id),
        "1",
        ex=AGENT_PRESENCE_TTL_SECONDS,
    )


def mark_agent_offline(tenant_id: int, user_id: int) -> None:
    """坐席登出或 WS 断开 -> 主动删除 presence key。"""
    get_redis().delete(_online_key(tenant_id, user_id))


def has_online_agent(tenant_id: int) -> bool:
    """租户是否存在仍未过期的客服 presence key。"""
    prefix = f"{ONLINE_AGENTS_PREFIX}{tenant_id}:*"
    redis_client = get_redis()
    return any(redis_client.ttl(key) > 0 for key in redis_client.scan_iter(prefix, count=10))


def online_agent_count(tenant_id: int) -> int:
    """返回仍在 TTL 内的在线坐席数；Redis 故障时保守返回 0。"""
    try:
        prefix = f"{ONLINE_AGENTS_PREFIX}{tenant_id}:*"
        redis_client = get_redis()
        return sum(1 for key in redis_client.scan_iter(prefix, count=20) if redis_client.ttl(key) > 0)
    except Exception:
        return 0


def estimate_wait_minutes(db: DbSession, tenant_id: int) -> dict:
    """根据当前队列、在线坐席和近期人工会话耗时估算等待时间。"""
    waiting = db.query(Session).filter(
        Session.tenant_id == tenant_id,
        Session.status == "waiting_human",
    ).count()
    online = online_agent_count(tenant_id)
    recent = (
        db.query(Session)
        .filter(
            Session.tenant_id == tenant_id,
            Session.assignee_id.is_not(None),
            Session.status.in_(["resolved", "closed"]),
        )
        .order_by(Session.updated_at.desc())
        .limit(30)
        .all()
    )
    durations = [
        max(60.0, (item.updated_at - item.created_at).total_seconds())
        for item in recent
        if item.updated_at and item.created_at and item.updated_at >= item.created_at
    ]
    average_seconds = sum(durations) / len(durations) if durations else 4 * 60
    capacity = max(online, 1)
    estimate = max(1, round((max(waiting, 1) / capacity) * average_seconds / 60))
    return {
        "estimated_wait_minutes": min(estimate, 60),
        "queue_length": waiting,
        "online_agents": online,
    }


def cleanup_expired_agent_presence() -> int:
    """兜底清除异常的无 TTL presence 及旧版 Set key；正常过期 key 由 Redis 自动删除。"""
    redis_client = get_redis()
    removed = 0
    for key in redis_client.scan_iter(f"{ONLINE_AGENTS_PREFIX}*"):
        text_key = key.decode() if isinstance(key, bytes) else str(key)
        is_legacy_set = len(text_key.split(":")) == 3
        if is_legacy_set or redis_client.ttl(key) == -1:
            removed += int(redis_client.delete(key) or 0)
    return removed


# ─── 会话生命周期 ───

def create_session(
    db: DbSession,
    *,
    tenant_id: int,
    end_user_id: str | None = None,
    channel: str = "web",
) -> dict:
    """创建新会话并初始化 Redis 上下文。"""
    # readonly/closed/pending_review 租户拒绝新会话
    from app.services.tenant_service import require_active_tenant
    require_active_tenant(db, tenant_id)

    session = session_crud.create_session(db, tenant_id=tenant_id, end_user_id=end_user_id)
    db.commit()
    # 初始化空的 Redis 上下文
    save_context(session.id, [])
    from app.services.analytics_service import track_event
    track_event(
        "session_created",
        tenant_id=tenant_id,
        properties={"session_id": session.id, "channel": channel, "intent": session.intent or "unknown"},
    )
    return {"session_id": session.id, "status": session.status, "created_at": session.created_at}


def get_session_detail(
    db: DbSession, *, session_id: int, tenant_id: int
) -> dict:
    """获取会话详情（含消息列表）。会话不存在抛 SessionNotFound。"""
    session = session_crud.get_by_id(db, session_id, tenant_id)
    if session is None:
        raise SessionNotFound(f"会话不存在: {session_id}")

    messages = session_crud.get_messages(db, session_id)
    return {
        "session_id": session.id,
        "status": session.status,
        "intent": session.intent,
        "sentiment": session.sentiment,
        "message_count": len(messages),
        "created_at": session.created_at,
        # 前端详情页渲染时间用（此前只改 schema 未填值 → 前端 formatDate(null) 显示 1970）
        "updated_at": session.updated_at,
        "messages": [
            {
                "message_id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


def _recent_business_text(messages) -> str:
    """拼接最近对话，供业务编号扫描。每条最多取前 2000 字。"""
    return "\n".join(
        message.content[:2000]
        for message in messages[-40:]
        if message.role in {"user", "assistant", "agent"}
    )


def _detected_business_refs(text: str) -> dict:
    """只识别文本里明确出现的订单号、工单号和手机号尾号。"""
    order_numbers = _ORDER_NO_RE.findall(text)
    ticket_numbers = _TICKET_NO_RE.findall(text)
    full_phones = _PHONE_RE.findall(text)
    last4_values = _LAST4_RE.findall(text)
    return {
        "order_no": order_numbers[-1].upper() if order_numbers else None,
        "ticket_no": ticket_numbers[-1].upper() if ticket_numbers else None,
        "phone_last4": (
            full_phones[-1][-4:]
            if full_phones
            else (last4_values[-1] if last4_values else None)
        ),
    }


def _order_for_context(db: DbSession, tenant_id: int, order_no: str | None):
    """按租户和订单号精确查找，不猜最近一单。"""
    if not order_no:
        return None
    from app.models.order import Order

    return db.query(Order).filter(
        Order.tenant_id == tenant_id,
        Order.order_no == order_no,
    ).first()


def _ticket_for_context(db: DbSession, tenant_id: int, ticket_no: str | None):
    """按租户和工单号精确查找。"""
    if not ticket_no:
        return None
    from app.models.ticket import Ticket

    return db.query(Ticket).filter(
        Ticket.tenant_id == tenant_id,
        Ticket.ticket_no == ticket_no,
    ).first()


def _recent_refunds(db: DbSession, order) -> list:
    """返回该订单最近 5 条退款审批。没有订单时为空。"""
    if order is None:
        return []
    from app.models.refund_approval import RefundApproval

    return db.query(RefundApproval).filter(
        RefundApproval.order_id == order.id
    ).order_by(RefundApproval.created_at.desc()).limit(5).all()


def _order_context_view(order) -> dict | None:
    """坐席可见的订单字段。不包含完整手机号。"""
    if order is None:
        return None
    return {
        "id": order.id,
        "order_no": order.order_no,
        "amount": float(order.amount),
        "paid_amount": float(order.paid_amount),
        "status": order.status,
        "tracking_no": order.tracking_no,
        "created_by": order.created_by,
    }


def _ticket_context_view(ticket) -> dict | None:
    """坐席可见的工单字段。"""
    if ticket is None:
        return None
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
    }


def _refund_context_views(refunds) -> list[dict]:
    """坐席可见的退款审批摘要。"""
    return [
        {
            "id": item.id,
            "amount": float(item.amount),
            "reason": item.reason,
            "status": item.status,
            "created_at": item.created_at,
        }
        for item in refunds
    ]


def _business_actions(order, refunds) -> dict:
    """根据订单状态和在途退款决定坐席可点的操作。资金操作仍要重新核验手机号。"""
    return {
        "can_create_ticket": True,
        "can_request_refund": bool(
            order is not None
            and order.status in {"paid", "shipped"}
            and not any(item.status in {"pending", "refunding"} for item in refunds)
        ),
        "can_ship": bool(order is not None and order.status == "paid"),
        "requires_phone_verification": True,
    }


def get_session_business_context(
    db: DbSession,
    *,
    session_id: int,
    tenant_id: int,
) -> dict:
    """从当前会话中提取业务编号，并返回可安全操作的订单/工单上下文。

    只按会话文本里明确出现的业务编号查询，不用“最近一单”猜测客户身份；
    手机号最多回传尾号，坐席发起资金操作时仍必须重新输入完整号码完成双因子校验。
    """
    session = session_crud.get_by_id(db, session_id, tenant_id)
    if session is None:
        raise SessionNotFound(f"会话不存在: {session_id}")
    messages = session_crud.get_messages(db, session_id)
    detected = _detected_business_refs(_recent_business_text(messages))
    order = _order_for_context(db, tenant_id, detected["order_no"])
    ticket = _ticket_for_context(db, tenant_id, detected["ticket_no"])
    refunds = _recent_refunds(db, order)
    return {
        "session_id": session_id,
        "detected": detected,
        "order": _order_context_view(order),
        "ticket": _ticket_context_view(ticket),
        "refunds": _refund_context_views(refunds),
        "actions": _business_actions(order, refunds),
    }


def is_session_expired(session: Session) -> bool:
    """检查会话是否超过空闲超时时间。

    """
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    delta = now - session.updated_at
    return delta.total_seconds() > CONTEXT_TTL_SECONDS


# ─── 会话生命周期 ───

# CLOSED 会话续聊窗口（PRD §3.7）: 关闭后 24h 内追问可重开，超期则需新建会话
CLOSED_REOPEN_HOURS = 24


class SessionNotFound(Exception):
    """会话不存在 -> 404."""


class SessionHumanControlled(Exception):
    """会话正在由人工客服处理，用户消息不得进入智能体链路 -> 409。"""


def ensure_agent_execution_allowed(session: Session) -> None:
    """在任何 Agent 执行或用户消息落库前阻断人工接管态。"""
    if session.status == "human_handling":
        raise SessionHumanControlled("会话正在由人工客服处理，请等待客服回复")


class SessionReopenExpired(Exception):
    """CLOSED 会话超过续聊窗口（24h）-> 无法重开。"""


class SessionNotClosed(Exception):
    """会话非 closed 状态，不可提交 CSAT -> 403."""


class CSATAlreadyExists(Exception):
    """该会话已提交过 CSAT -> 409."""


class InvalidFeedbackScore(Exception):
    """feedback score 仅允许 1(有用) 或 -1(无用) -> 422."""


class MessageNotFound(Exception):
    """消息不存在 -> 404."""


class FeedbackAlreadyExists(Exception):
    """该消息已有反馈 -> 409."""


def handle_closed_reply(db: DbSession, *, session_id: int, tenant_id: int) -> dict:
    """处理 CLOSED 会话的追问续聊（PRD §3.7）。

    业务规则:
      - 会话非 closed -> 无需重开，直接返回（调用方正常处理消息）
      - closed 且 updated_at 在 24h 内 -> 重开为 agent_handling 继续对话
      - closed 且超过 24h -> 抛 SessionReopenExpired（调用方引导用户新建会话）

    设计决策:
      - 用 updated_at 判定关闭时间: status 变更为 closed 时 onupdate 刷新 updated_at，
        即"关闭后多久"的基准。续聊窗口 24h 与空闲收尾 60min 解耦，互不影响。
      - 重开为 agent_handling 而非 created: 保留原会话上下文与意图，AI 直接接管。
      - 只 flush 不 commit: 事务由 API 层控制（重开 + 落消息需同事务）。

    Args:
        db: 数据库会话
        session_id: 会话 ID
        tenant_id: 租户 ID

    Returns:
        dict: reopened(bool) / session_id / status / reason(非关闭时的说明)
    """
    import datetime

    session = session_crud.get_by_id(db, session_id, tenant_id)
    if session is None:
        raise SessionNotFound(f"会话不存在: {session_id}")

    if session.status != "closed":
        # 非关闭状态无需重开，调用方正常处理
        return {"reopened": False, "session_id": session_id, "status": session.status,
                "reason": "not_closed"}

    # 检查 24h 续聊窗口 —— updated_at 即关闭时间（status 变更触发 onupdate）
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    delta = now - session.updated_at
    if delta.total_seconds() > CLOSED_REOPEN_HOURS * 3600:
        raise SessionReopenExpired(
            f"会话已关闭超过 {CLOSED_REOPEN_HOURS}h，无法续聊，请新建会话"
        )

    # 24h 内 → 重开为 agent_handling，保留原上下文供 AI 接管
    session.status = "agent_handling"
    db.flush()
    return {"reopened": True, "session_id": session_id, "status": "agent_handling"}


# ─── 快捷回复/ CSAT（规格 007）/ 回答反馈───
# 分层收口（M11）: API 层不再直接 crud+commit，统一由 service 封装
# crud 操作 + commit + 业务校验，业务异常抛 service 自定义异常由 API 层映射 HTTP。

def create_quick_reply(
    db: DbSession, *, tenant_id: int, content: str, shortcut: str | None = None
) -> dict:
    """创建快捷回复模板（PRD §3.7）: crud 创建 + commit。返回 {id, content, shortcut}。"""
    q = quick_reply_crud.create(db, tenant_id=tenant_id, content=content, shortcut=shortcut)
    db.commit()
    return {"id": q.id, "content": q.content, "shortcut": q.shortcut}


def submit_csat(
    db: DbSession, *,
    session_id: int, tenant_id: int, score: int, comment: str | None = None,
) -> dict:
    """提交 CSAT 满意度评价（规格 007 US3）: 仅 closed 会话可评价，同一会话仅一次。

    Raises:
        SessionNotFound: 会话不存在 -> 404
        SessionNotClosed: 会话非 closed 状态 -> 403
        CSATAlreadyExists: 已评价过 -> 409
    """
    s = session_crud.get_by_id(db, session_id, tenant_id)
    if s is None:
        raise SessionNotFound(f"会话不存在: {session_id}")
    if s.status != "closed":
        raise SessionNotClosed("仅已关闭会话可评价")
    if csat_crud.get_by_session(db, session_id, tenant_id) is not None:
        raise CSATAlreadyExists("该会话已评价")

    csat = csat_crud.create_csat(db, session_id=session_id, score=score, comment=comment)
    db.commit()
    return {"id": csat.id, "session_id": session_id, "score": score}


def submit_feedback(
    db: DbSession, *,
    message_id: int, tenant_id: int, score: int, comment: str | None = None,
) -> dict:
    """提交单条消息回答反馈（PRD §3.8）: score 仅 ±1，同一消息仅一次。

    校验顺序与原 API 层一致: 先判 score=0，再查消息存在性，最后查去重。

    Raises:
        InvalidFeedbackScore: score 为 0 -> 422
        MessageNotFound: 消息不存在 -> 404
        FeedbackAlreadyExists: 已反馈过 -> 409
    """
    if score == 0:
        raise InvalidFeedbackScore("score 仅允许 1(有用) 或 -1(无用)")
    msg = session_crud.get_message_by_id(db, message_id, tenant_id)
    if msg is None:
        raise MessageNotFound(f"消息不存在: {message_id}")
    if feedback_crud.get_by_message(db, message_id, tenant_id) is not None:
        raise FeedbackAlreadyExists("该消息已反馈")

    fb = feedback_crud.create_feedback(db, message_id=message_id, score=score, comment=comment)
    db.commit()
    from app.services.analytics_service import track_event
    track_event(
        "answer_feedback",
        tenant_id=tenant_id,
        properties={
            "session_id": msg.session_id,
            "message_id": message_id,
            "feedback": "like" if score > 0 else "dislike",
        },
    )
    return {
        "id": fb.id,
        "message_id": message_id,
        "score": fb.score,
        "bad_case": fb.bad_case,
    }
