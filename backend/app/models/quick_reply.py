"""
backend/app/models/quick_reply.py

职责: quick_replies 表 ORM 模型定义（坐席快捷回复模板）
对应日程: 022-quick-reply / PRD 3.7

核心设计:
  坐席常用回复模板，按租户隔离。坐席在人工接待时一键选择模板快速回复，提升效率。
  关键字段: tenant_id(隔离键)、content(模板内容)、shortcut(可选快捷指令前缀)
"""

from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class QuickReply(BaseModel):
    """快捷回复模板表 - 坐席常用回复语."""

    __tablename__ = "quick_replies"

    # 租户隔离键（所有查询必须带此条件）
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # 模板内容（坐席一键发送的回复文本）
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 快捷指令前缀（可选，如 "/refund" -> "您的退款已受理..."）
    shortcut: Mapped[str | None] = mapped_column(String(50), nullable=True)
