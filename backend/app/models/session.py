"""
backend/app/models/session.py

职责: sessions 表 ORM 模型定义（会话状态机）
对应日程: 003-session-routing（007-session-lifecycle 扩展人工接待状态）

核心设计:
  一次用户与智能体的完整对话，状态机驱动流转：
    created -> routing -> agent_handling -> resolved -> closed
    007 扩展: waiting_human <-> human_handling（人工接待回路）
  关键字段: tenant_id(隔离键)、status(状态机)、intent(意图)、confidence(路由置信度)、
            sentiment(情绪)、assignee_id(坐席 ID，007 启用)
"""

from sqlalchemy import BigInteger, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Session(BaseModel):
    """会话表 - 一次用户与智能体的完整对话."""

    __tablename__ = "sessions"

    # 租户隔离键
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # 会话状态: created / routing / agent_handling / resolved / closed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    # 最后识别的意图: knowledge_consult / ticket_query / order_query / sensitive_action
    intent: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 最后路由置信度 (0.0 ~ 1.0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 最后识别的情绪: normal / unhappy / angry
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 坐席 ID（007-session-lifecycle 启用人工认领，本日程预留）
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", name="fk_sessions_assignee_id"), nullable=True)  # T27 FK
    # 终端用户标识（访客 ID，前端生成；用于长期记忆按用户隔离，009 长期记忆修复）
    end_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
