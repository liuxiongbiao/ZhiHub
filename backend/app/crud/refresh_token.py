"""
backend/app/crud/refresh_token.py

职责: refresh_tokens 表的查询、原子消费与批量撤销。
"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.refresh_token import RefreshToken


def get_by_hash(db: Session, token_hash: str) -> RefreshToken | None:
    return db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()


def create(
    db: Session,
    *,
    token_hash: str,
    family_id: str,
    user_id: int,
    tenant_id: int,
    expires_at: datetime,
    parent_token_id: int | None = None,
) -> RefreshToken:
    token = RefreshToken(
        token_hash=token_hash,
        family_id=family_id,
        user_id=user_id,
        tenant_id=tenant_id,
        expires_at=expires_at,
        parent_token_id=parent_token_id,
        status="active",
    )
    db.add(token)
    db.flush()
    return token


def mark_used_if_active(db: Session, token_id: int, used_at: datetime) -> bool:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == token_id, RefreshToken.status == "active")
        .values(status="used", used_at=used_at)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def revoke_family(db: Session, family_id: str, revoked_at: datetime) -> int:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.status == "active")
        .values(status="revoked", revoked_at=revoked_at)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


def revoke_user(
    db: Session, user_id: int, revoked_at: datetime, tenant_id: int | None = None
) -> int:
    conditions = [RefreshToken.user_id == user_id, RefreshToken.status == "active"]
    if tenant_id is not None:
        conditions.append(RefreshToken.tenant_id == tenant_id)
    result = db.execute(
        update(RefreshToken)
        .where(*conditions)
        .values(status="revoked", revoked_at=revoked_at)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount
