"""
backend/app/crud/csat.py

职责: csat_evaluations 表的数据库访问层（满意度评价创建、按会话查询去重）

核心设计:
  csat_evaluations 无 tenant_id 列，通过 join session.tenant_id 隔离（防御纵深：
  crud 层 get_by_session 自带 tenant_id 过滤，不依赖调用方记忆）。同一会话仅一条。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.csat import CsatEvaluation
from app.models.session import Session


def create_csat(
    db: Session, *, session_id: int, score: int, comment: str | None = None,
    message_id: int | None = None, feedback: str | None = None,
) -> CsatEvaluation:
    """创建 CSAT 评价记录。"""
    csat = CsatEvaluation(
        session_id=session_id, score=score, comment=comment,
        message_id=message_id, feedback=feedback or "like",
    )
    db.add(csat)
    db.flush()
    return csat


def get_by_session(db: Session, session_id: int, tenant_id: int) -> CsatEvaluation | None:
    """按会话 + tenant_id 查询 CSAT（去重 + 防御纵深：crud 层自带租户隔离）。"""
    return db.execute(
        select(CsatEvaluation)
        .join(Session, Session.id == CsatEvaluation.session_id)
        .where(CsatEvaluation.session_id == session_id, Session.tenant_id == tenant_id)
    ).scalar_one_or_none()
