"""
backend/app/models/csat.py

职责: csat_evaluations 表 ORM 模型定义（满意度评价 + 点赞/点踩）
对应日程: 007-session-lifecycle

核心设计:
  会话级满意度评价，无 tenant_id 列，通过 session.tenant_id 间接隔离（调用方先校验会话归属）。
  关键字段: session_id(所属会话)、message_id(关联消息，可空)、feedback(like/dislike)、score(1-5)、comment
"""
from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import BaseModel

class CsatEvaluation(BaseModel):
    __tablename__ = "csat_evaluations"
    session_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sessions.id", name="fk_csat_session_id"), nullable=False, unique=True, index=True)  # 所属会话（unique：同一会话仅一条评价）
    message_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("messages.id", name="fk_csat_message_id"), nullable=True)  # 关联消息（可空）
    feedback: Mapped[str] = mapped_column(String(10), nullable=False)  # like / dislike
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5 分
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)  # 文字评价
