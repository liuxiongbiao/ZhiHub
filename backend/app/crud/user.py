"""
backend/app/crud/user.py

职责: users 表的数据库访问层（平台账号存取）
对应日程: 001-account-tenant / PRD 3.1 账户体系扩展

核心设计:
  纯数据存取无业务判断，只 flush 不 commit，事务边界由 Service 层统一控制。
  注意: User 是平台账号无 tenant_id 列，租户归属通过 TenantMember 关联表体现，故本文件不做租户过滤。
  PII 加密: phone/email 列 AES-GCM 加密（密文不确定），等值查询走 *_hash 列（HMAC-SHA256 确定性）。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import phone_hash, pii_hash
from app.models.user import User


def get_by_phone(db: Session, phone: str) -> User | None:
    """按手机号查询用户（注册查重/登录查找）。

    查询走 phone_hash 列（HMAC-SHA256 确定性哈希）而非 phone 密文列，
    因为 AES-GCM nonce 随机 -> 密文不确定 -> WHERE phone = ? 无法匹配。
    """
    return db.execute(
        select(User).where(User.phone_hash == phone_hash(phone))
    ).scalar_one_or_none()


def get_by_email(db: Session, email: str) -> User | None:
    """按邮箱查询用户（邮箱注册查重/邮箱登录）。

    查询走 email_hash 列（HMAC-SHA256 确定性哈希）而非 email 密文列，
    原理同 get_by_phone。
    """
    return db.execute(
        select(User).where(User.email_hash == pii_hash(email))
    ).scalar_one_or_none()


def get_by_id(db: Session, user_id: int) -> User | None:
    """按主键查询用户（deps.get_current_user 用）。"""
    return db.get(User, user_id)


def get_by_openid(db: Session, openid: str) -> User | None:
    """按微信 openid 查询用户（扫码登录查找绑定）。

    openid 列有 unique 索引，等值查询高效。未绑定返回 None。
    """
    return db.execute(
        select(User).where(User.wechat_openid == openid)
    ).scalar_one_or_none()


def create(
    db: Session,
    *,
    password_hash: str,
    phone: str | None = None,
    email: str | None = None,
    role_default: str = "admin",
) -> User:
    """创建用户并 flush（拿到自增 id），但不 commit -- 事务由 Service 层控制。

    phone/email 均可选: 手机号注册传 phone，邮箱注册传 email，两者可共存（手机号注册后绑定邮箱）。
    """
    user = User(
        phone=phone,
        email=email,
        password_hash=password_hash,
        role_default=role_default,
    )
    db.add(user)
    db.flush()
    return user
