"""
backend/app/crud/session.py

职责: sessions / messages 表的数据库访问层（会话状态查询、消息持久化）

核心设计:
  会话查询强制 tenant_id 过滤；消息表无 tenant_id 列，靠调用方先校验会话归属再查消息（间接隔离）。
  只 flush 不 commit，事务由 Service 层控制。
"""

from sqlalchemy import func, select, case
from sqlalchemy.orm import Session as DbSession

from app.models.message import Message
from app.models.session import Session


# ─── Session CRUD ───

def create_session(db: DbSession, *, tenant_id: int, end_user_id: str | None = None) -> Session:
    """创建新会话（初始状态 created，可带终端用户标识）。"""
    session = Session(tenant_id=tenant_id, status="created", end_user_id=end_user_id)
    db.add(session)
    db.flush()
    return session


def get_by_id(db: DbSession, session_id: int, tenant_id: int) -> Session | None:
    """按 ID + tenant_id 查询会话（租户隔离）。"""
    return db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()


def list_sessions(
    db: DbSession,
    tenant_id: int,
    *,
    status: str | None = None,
    sentiment: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """分页查询会话列表（含消息数量），可选按 status/sentiment 筛选。返回 (items, total)。

     排序规则: 愤怒情绪（sentiment='angry'）优先置顶，同情绪内按创建时间倒序。
    设计动机: 愤怒用户流失风险高，坐席工作台应优先展示待介入。
    """
    query = select(Session).where(Session.tenant_id == tenant_id)
    if status:
        query = query.where(Session.status == status)
    if sentiment:
        query = query.where(Session.sentiment == sentiment)

    # 总数
    count_query = select(func.count()).select_from(Session).where(Session.tenant_id == tenant_id)
    if status:
        count_query = count_query.where(Session.status == status)
    if sentiment:
        count_query = count_query.where(Session.sentiment == sentiment)
    total = db.execute(count_query).scalar() or 0

    # angry 优先排序 —— case 表达式把 angry 映射为 0（排前），其余 1（排后）
    angry_priority = case((Session.sentiment == "angry", 0), else_=1)
    # 分页
    sessions = db.execute(
        query.order_by(angry_priority, Session.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    items = []
    for s in sessions:
        msg_count = db.execute(
            select(func.count()).select_from(Message).where(Message.session_id == s.id)
        ).scalar() or 0
        items.append({
            "session_id": s.id,
            "status": s.status,
            "intent": s.intent,
            "sentiment": s.sentiment,
            "message_count": msg_count,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        })

    return items, total


def update_status(db: DbSession, session: Session, status: str) -> None:
    """更新会话状态。"""
    session.status = status
    db.flush()


def update_intent(
    db: DbSession,
    session: Session,
    *,
    intent: str,
    confidence: float | None = None,
    sentiment: str | None = None,
) -> None:
    """更新会话的意图/置信度/情绪。"""
    session.intent = intent
    if confidence is not None:
        session.confidence = confidence
    if sentiment is not None:
        session.sentiment = sentiment
    db.flush()


# ─── Message CRUD ───

def create_message(
    db: DbSession,
    *,
    session_id: int,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    metadata: dict | None = None,
) -> Message:
    """创建消息记录。"""
    msg = Message(
        session_id=session_id,
        role=role,
        content=content,
        sources=sources,
        msg_metadata=metadata,
    )
    db.add(msg)
    db.flush()
    return msg


def get_messages(db: DbSession, session_id: int) -> list[Message]:
    """获取会话的全部消息（按时间正序）。

    注意: 仅按 session_id 过滤，未带 tenant_id；多租户隔离依赖调用方先用
    get_by_id(session_id, tenant_id) 校验会话归属后再调用本函数。
    """
    return list(
        db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
        ).scalars().all()
    )


def get_message_by_id(db: DbSession, message_id: int, tenant_id: int) -> Message | None:
    """按消息 ID + 租户查询消息（间接隔离：message -> session.tenant_id）。

    message 表无 tenant_id 列，通过 join sessions 表校验租户归属，
    确保跨租户无法访问他人消息（回答反馈等端点用）。
    """
    return db.execute(
        select(Message)
        .join(Session, Message.session_id == Session.id)
        .where(Message.id == message_id, Session.tenant_id == tenant_id)
    ).scalar_one_or_none()
