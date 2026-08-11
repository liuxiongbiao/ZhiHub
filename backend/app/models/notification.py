"""app/models/notification.py

站内信通知模型（closure-plan P1-5）：配额预警/转人工提醒等关键事件真实落库，
前端铃铛可查可已读。user_id 为空表示面向租户全员。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Notification(BaseModel):
    __tablename__ = "notifications"

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # None = 全员
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False, default="system")  # quota_warning|handoff|refund|system
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
