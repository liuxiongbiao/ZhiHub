"""
backend/app/crud/quick_reply.py

职责: quick_replies 表的数据库访问层（坐席快捷回复模板存取）
对应日程: 022-quick-reply / PRD 3.7

核心设计:
  按租户隔离，所有查询强制带 tenant_id。只 flush 不 commit，事务由调用方控制。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.quick_reply import QuickReply


def create(
    db: Session,
    *,
    tenant_id: int,
    content: str,
    shortcut: str | None = None,
) -> QuickReply:
    """创建快捷回复模板。"""
    reply = QuickReply(tenant_id=tenant_id, content=content, shortcut=shortcut)
    db.add(reply)
    db.flush()
    return reply


def list_by_tenant(db: Session, tenant_id: int) -> list[QuickReply]:
    """查询租户下全部快捷回复模板（按 id 升序）。"""
    return list(
        db.execute(
            select(QuickReply)
            .where(QuickReply.tenant_id == tenant_id)
            .order_by(QuickReply.id)
        ).scalars().all()
    )


def get_by_id(db: Session, reply_id: int, tenant_id: int) -> QuickReply | None:
    """按 ID + 租户查询快捷回复（租户隔离）。"""
    return db.execute(
        select(QuickReply).where(
            QuickReply.id == reply_id, QuickReply.tenant_id == tenant_id
        )
    ).scalar_one_or_none()
