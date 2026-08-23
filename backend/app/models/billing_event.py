"""
backend/app/models/billing_event.py

职责: billing_events 会话计费事件 ORM 模型，隔离会话结算与 Redis/账单投影。

核心设计:
  同一 session_id 只允许一条事件；事件先在会话事务内落库，再由消费者按 event_key
  幂等写入 Redis。status/attempts 是消费投影，不参与会话是否应计费的业务判断。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class BillingEvent(BaseModel):
    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_billing_events_session_id"),
        UniqueConstraint("event_key", name="uq_billing_events_event_key"),
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sessions.id", name="fk_billing_events_session_id"),
        nullable=False,
    )
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_amount: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

