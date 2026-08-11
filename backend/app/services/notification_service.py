"""app/services/notification_service.py

站内信服务（closure-plan P1-5）：创建/列表/未读数/已读。
配额预警、转人工提醒等触发点写库，前端铃铛轮询展示。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.notification import Notification

logger = logging.getLogger(__name__)


def create_notification(
    db: Session, *, tenant_id: int, title: str, content: str | None = None,
    type: str = "system", user_id: int | None = None,
) -> Notification:
    """写入一条站内信（tenant 隔离；user_id=None 表示全员）。"""
    n = Notification(
        tenant_id=tenant_id, user_id=user_id, title=title,
        content=content, type=type,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def list_notifications(
    db: Session, tenant_id: int, *, user_id: int | None = None,
    unread: bool = False, page: int = 1, page_size: int = 20,
) -> dict:
    """分页查询站内信（租户维度；user_id 为空时全员可见）。"""
    conds = [Notification.tenant_id == tenant_id]
    if user_id is not None:
        conds.append((Notification.user_id == user_id) | (Notification.user_id.is_(None)))
    if unread:
        conds.append(Notification.read_at.is_(None))
    total = db.execute(select(func.count()).select_from(Notification).where(*conds)).scalar() or 0
    items = db.execute(
        select(Notification).where(*conds)
        .order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return {
        "items": [
            {
                "id": n.id, "title": n.title, "content": n.content,
                "type": n.type, "read_at": n.read_at, "created_at": n.created_at,
            }
            for n in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


def unread_count(db: Session, tenant_id: int, *, user_id: int | None = None) -> int:
    """未读站内信数量。"""
    conds = [Notification.tenant_id == tenant_id, Notification.read_at.is_(None)]
    if user_id is not None:
        conds.append((Notification.user_id == user_id) | (Notification.user_id.is_(None)))
    return db.execute(select(func.count()).select_from(Notification).where(*conds)).scalar() or 0


def mark_read(db: Session, notification_id: int, tenant_id: int) -> bool:
    """标记已读（租户隔离）；不存在返回 False。"""
    n = db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.tenant_id == tenant_id
        )
    ).scalar_one_or_none()
    if n is None:
        return False
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    return True
