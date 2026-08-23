"""refund_outbox 表 ORM 模型：退款执行外发队列（唯一任务 + 领取租约）。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class RefundOutbox(BaseModel):
    __tablename__ = "refund_outbox"
    __table_args__ = (
        UniqueConstraint("approval_id", name="uq_refund_outbox_approval_id"),
    )

    approval_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("refund_approvals.id", name="fk_refund_outbox_approval_id"),
        nullable=False, index=True,
    )
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("orders.id", name="fk_refund_outbox_order_id"),
        nullable=False, index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    trade_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
