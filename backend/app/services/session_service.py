"""
backend/app/services/session_service.py

职责: 会话生命周期管理--创建、状态流转、Redis 上下文读写、轮次计数转人工、在线坐席 presence。

核心设计:
  四块功能：①Redis 上下文读写（最近 N 轮消息，TTL 30min 空闲超时）；②轮次计数（consecutive/total/last_intent），
  连续 3 轮可解决类意图未解决或累计 15 轮转人工；③在线坐席 presence（SADD/SREM 集合，判断有无坐席决定留言建单）；
  ④会话创建与详情。轮次计数知识咨询豁免 consecutive（允许探索性多轮），但计入 total（总上限保护）。

关键技术点:
  - Redis 上下文: 最近 10 轮消息，TTL 30min，滚动续期
  - 轮次计数三态: consecutive（连续同意图）/ total（累计）/ last_intent（判 streak）
  - presence 集合: SADD/SREM/SCARD 实现坐席在线判断，O(1) 复杂度
  - 转人工阈值: consecutive>=3 或 total>=15

依赖关系:
  - 上游: app/services/agent_service.py（上下文/轮次）、auth_service.py（presence）、app/api/v1/session.py
  - 下游: app/core/redis.py、app/crud/session.py、app/models/session.py
"""

import json
import time

from sqlalchemy.orm import Session as DbSession

from app.core.redis import get_redis
from app.crud import answer_feedback as feedback_crud
from app.crud import csat as csat_crud
from app.crud import quick_reply as quick_reply_crud
from app.crud import session as session_crud
from app.models.session import Session

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


# ─── 会话轮次计数（007-session-lifecycle US1 轮次触发转人工）───

# 可解决类意图：连续未解决达阈值即转人工；知识咨询豁免（允许探索性多轮）
RESOLVABLE_INTENTS = {"ticket_query", "order_query", "sensitive_action"}
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
      3. 知识咨询 -> consecutive=0（豁免探索性多轮）
      4. total 始终 +1，记录 last_intent

    设计决策:
      "连续3轮相同"= consecutive 达 3（第3轮触发，无需第4轮）。知识咨询豁免 consecutive
      是因为知识探索天然多轮（用户追问细节），不应因多轮转人工；但计入 total 防无限对话。

    可解决类意图：与上一轮相同 -> consecutive++（streak 累加）；不同 -> 新 streak 从 1 起。
    知识咨询 -> consecutive=0（豁免探索性多轮）。
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
        rounds["consecutive"] = 0  # 知识咨询豁免
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


# ─── 在线坐席 presence（007-session-lifecycle US3 留言建单触发判定）───

ONLINE_AGENTS_PREFIX = "agents:online:"


def _online_key(tenant_id: int) -> str:
    return f"{ONLINE_AGENTS_PREFIX}{tenant_id}"


def mark_agent_online(tenant_id: int, user_id: int) -> None:
    """坐席登录 -> SADD 加入租户在线集合。"""
    get_redis().sadd(_online_key(tenant_id), str(user_id))


def mark_agent_offline(tenant_id: int, user_id: int) -> None:
    """坐席登出 -> SREM 移出在线集合。"""
    get_redis().srem(_online_key(tenant_id), str(user_id))


def has_online_agent(tenant_id: int) -> bool:
    """租户是否有在线坐席（SCARD > 0）。决定留言是否建单--无坐席时留言自动建单。"""
    return get_redis().scard(_online_key(tenant_id)) > 0


# ─── 会话生命周期 ───

def create_session(db: DbSession, *, tenant_id: int, end_user_id: str | None = None) -> dict:
    """创建新会话并初始化 Redis 上下文。"""
    session = session_crud.create_session(db, tenant_id=tenant_id, end_user_id=end_user_id)
    db.commit()
    # 初始化空的 Redis 上下文
    save_context(session.id, [])
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
        # T28: 前端详情页渲染时间用（此前只改 schema 未填值 -> 前端 formatDate(null) 显示 1970）
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


def is_session_expired(session: Session) -> bool:
    """检查会话是否超过空闲超时时间。

    """
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    delta = now - session.updated_at
    return delta.total_seconds() > CONTEXT_TTL_SECONDS


# ─── 会话生命周期 ───

# 024: CLOSED 会话续聊窗口（PRD 3.7）: 关闭后 24h 内追问可重开，超期则需新建会话
CLOSED_REOPEN_HOURS = 24


class SessionNotFound(Exception):
    """会话不存在 -> 404."""


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
    """处理 CLOSED 会话的追问续聊（024 PRD 3.7）。

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

    # 检查 24h 续聊窗口 -- updated_at 即关闭时间（status 变更触发 onupdate）
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    delta = now - session.updated_at
    if delta.total_seconds() > CLOSED_REOPEN_HOURS * 3600:
        raise SessionReopenExpired(
            f"会话已关闭超过 {CLOSED_REOPEN_HOURS}h，无法续聊，请新建会话"
        )

    # 24h 内 -> 重开为 agent_handling，保留原上下文供 AI 接管
    session.status = "agent_handling"
    db.flush()
    return {"reopened": True, "session_id": session_id, "status": "agent_handling"}


# ─── 快捷回复（022）/ CSAT（007）/ 回答反馈（021）───
# 分层收口（M11）: API 层不再直接 crud+commit，统一由 service 封装
# crud 操作 + commit + 业务校验，业务异常抛 service 自定义异常由 API 层映射 HTTP。

def create_quick_reply(
    db: DbSession, *, tenant_id: int, content: str, shortcut: str | None = None
) -> dict:
    """创建快捷回复模板（022 PRD 3.7）: crud 创建 + commit。返回 {id, content, shortcut}。"""
    q = quick_reply_crud.create(db, tenant_id=tenant_id, content=content, shortcut=shortcut)
    db.commit()
    return {"id": q.id, "content": q.content, "shortcut": q.shortcut}


def submit_csat(
    db: DbSession, *,
    session_id: int, tenant_id: int, score: int, comment: str | None = None,
) -> dict:
    """提交 CSAT 满意度评价（007 US3）: 仅 closed 会话可评价，同一会话仅一次。

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
    """提交单条消息回答反馈（021 PRD 3.8）: score 仅 ±1，同一消息仅一次。

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
    return {
        "id": fb.id,
        "message_id": message_id,
        "score": fb.score,
        "bad_case": fb.bad_case,
    }
