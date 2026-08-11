"""
backend/app/crud/answer_feedback.py

职责: answer_feedback 表的数据库访问层（单条消息 👍/👎 评价存取）
对应日程: 021-answer-feedback / PRD 3.8

核心设计:
  无 tenant_id 列，通过 join message->session.tenant_id 隔离（防御纵深：
  crud 层 get_by_message 自带 tenant_id 过滤）。一条消息最多一条反馈。
  只 flush 不 commit，事务由 Service/API 层控制。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.answer_feedback import AnswerFeedback
from app.models.message import Message
from app.models.session import Session


def create_feedback(
    db: Session,
    *,
    message_id: int,
    score: int,
    comment: str | None = None,
) -> AnswerFeedback:
    """创建回答反馈记录。

    score=1（👍）bad_case=False；score=-1（👎）bad_case=True 标记低质回答待复盘。
    """
    feedback = AnswerFeedback(
        message_id=message_id,
        score=score,
        bad_case=(score < 0),
        comment=comment,
    )
    db.add(feedback)
    db.flush()
    return feedback


def get_by_message(db: Session, message_id: int, tenant_id: int) -> AnswerFeedback | None:
    """按消息 + tenant_id 查询反馈（去重 + 防御纵深：经 message->session.tenant_id 隔离）。"""
    return db.execute(
        select(AnswerFeedback)
        .join(Message, Message.id == AnswerFeedback.message_id)
        .join(Session, Session.id == Message.session_id)
        .where(AnswerFeedback.message_id == message_id, Session.tenant_id == tenant_id)
    ).scalar_one_or_none()
