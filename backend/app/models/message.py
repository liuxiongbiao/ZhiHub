"""
backend/app/models/message.py

职责: messages 表 ORM 模型定义（会话中的一条消息）
对应日程: 003-session-routing

核心设计:
  会话消息流水，role 区分消息来源，sources/metadata 以 JSON 存检索引用与运行指标。
  关键字段: session_id(所属会话)、role(user/assistant/agent/system)、content、sources(引用来源 JSON)、
            msg_metadata(附加信息 JSON，DB 列名为 metadata)
"""

from sqlalchemy import BigInteger, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Message(BaseModel):
    """消息表 - 会话中的一条消息."""

    __tablename__ = "messages"

    # 所属会话
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sessions.id"), nullable=False, index=True
    )
    # 角色: user / assistant / agent / system
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # 消息文本内容
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 引用来源 JSON: [{"doc": "xx.pdf", "chunk_index": 0, "text": "..."}]
    sources: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 附加信息 JSON: {"tokens": 123, "elapsed_ms": 234, "intent": "..."}
    # 列名映射为 metadata（避开 SQLAlchemy 保留字）
    msg_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
