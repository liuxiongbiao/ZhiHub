"""
backend/app/models/refund_approval_event.py

职责: refund_approval_events 不可变审批事件模型

核心设计:
  每次申请、初审、终审、拒绝和取消只 INSERT 新事件；当前状态由
  refund_approvals 聚合表投影。ORM 事件监听阻止应用层 UPDATE/DELETE，
  生产 MySQL 由 Alembic 创建触发器作数据库兜底。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, event, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RefundApprovalEvent(Base):
    __tablename__ = "refund_approval_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    approval_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("refund_approvals.id", name="fk_refund_approval_events_approval_id"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_refund_approval_events_actor_id"),
        nullable=True,
    )
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


def _reject_mutation(_mapper, _connection, _target) -> None:
    """审批事件是账务审计证据，创建后禁止更新或删除。"""
    raise ValueError("退款审批事件不可变：禁止 UPDATE/DELETE")


event.listen(RefundApprovalEvent, "before_update", _reject_mutation)
event.listen(RefundApprovalEvent, "before_delete", _reject_mutation)
