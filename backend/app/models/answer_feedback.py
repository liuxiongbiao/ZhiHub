"""
backend/app/models/answer_feedback.py

职责: answer_feedback 表 ORM 模型定义（单条消息的回答反馈 👍/👎 + bad_case 标记）
对应日程: 021-answer-feedback / PRD 3.8

核心设计:
  终端用户对 Agent 回复打分（like/dislike），👎 标记 bad_case 供后续人工复盘与模型调优。
  无 tenant_id 列，通过 message -> session.tenant_id 间接隔离（调用方先校验消息归属）。
  关键字段: message_id(关联消息)、score(1=👍 / -1=👎)、bad_case(👎 时 True，标记低质回答)
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class AnswerFeedback(BaseModel):
    """回答反馈表 - 单条消息的 👍/👎 评价."""

    __tablename__ = "answer_feedback"

    # 关联消息 ID（一条消息最多一条反馈，unique 约束兜底去重）
    message_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("messages.id", name="fk_answer_feedback_message_id"), nullable=False, unique=True, index=True)  # T27 FK
    # 评分: 1=👍（有用）/ -1=👎（无用）
    score: Mapped[int] = mapped_column(nullable=False)
    # bad_case 标记: 👎 时 True，标记为低质回答待人工复盘
    bad_case: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 文字反馈（可选，👎 时用户可补充原因）
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
