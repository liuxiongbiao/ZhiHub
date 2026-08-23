"""
backend/app/models/session.py

职责: sessions 表 ORM 模型定义（会话状态机）

核心设计:
  一次用户与智能体的完整对话，状态机驱动流转：
    created -> routing -> agent_handling -> resolved -> closed
    规格 007 扩展: waiting_human <-> human_handling（人工接待回路）
  关键字段: tenant_id(隔离键)、status(状态机)、intent(意图)、confidence(路由置信度)、
            sentiment(情绪)、assignee_id(坐席 ID，规格 007 启用)
"""

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, String
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
    # 坐席 ID（规格 007 启用人工认领，本日程预留）
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", name="fk_sessions_assignee_id"), nullable=True)  #  FK
    # 终端用户标识（访客 ID，前端生成；用于长期记忆按用户隔离，规格 009 长期记忆修复）
    end_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 计费口径（PRD §3.4/3.9）：会话进入 RESOLVED 且产生有效回复才计费。
    # effective_reply=True 表示会话内已产生有效回复（非转人工、非 LLM 失败兜底、非降级提示）；
    # billed=True 表示该会话配额已计入，防止 RESOLVED 收尾重复计费。
    effective_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    billed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
