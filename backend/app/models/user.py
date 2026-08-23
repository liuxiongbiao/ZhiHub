"""
backend/app/models/user.py

职责: users 表 ORM 模型定义（平台账号：管理员/坐席）

核心设计:
  平台账号表，支持手机号+密码、手机验证码、邮箱+密码三种登录方式，一个 User 可通过
  TenantMember 加入多个租户。
  关键字段: phone(AES-GCM 加密存储，可空--邮箱注册用户无手机号)、phone_hash(HMAC 确定性哈希)、
            email(AES-GCM 加密存储，可空)、email_hash(HMAC 确定性哈希，用于邮箱等值查询/唯一约束)、
            wechat_openid(微信 openid，可空，预留给 #12 微信登录)、
            password_hash(bcrypt)、login_fail_count(失败计数)、locked_until(锁定截止)
  PII 加密: phone/email 列用 EncryptedString（AES-256-GCM），密文不确定（nonce 随机），
            故 unique/index 移到 *_hash 列；登录查重走 *_hash 等值查询。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.crypto import EncryptedString, phone_hash, pii_hash
from app.models.base import BaseModel


class User(BaseModel):
    __tablename__ = "users"

    # 手机号作为登录名，AES-GCM 加密存储（PRD §3.1/4.3 PII 静态加密）
    # 密文不确定（nonce 随机），unique/index 放在 phone_hash 列而非此列
    # nullable=True: 邮箱注册用户无手机号（PRD §3.1 账户体系扩展 #11）
    phone: Mapped[Optional[str]] = mapped_column(EncryptedString(255), nullable=True)
    # 手机号确定性哈希（HMAC-SHA256），用于等值查询（登录/查重）和唯一约束
    phone_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)
    # 邮箱（AES-GCM 加密存储），可空——手机号注册用户初始无邮箱，可后续绑定（#15）
    email: Mapped[Optional[str]] = mapped_column(EncryptedString(255), nullable=True)
    # 邮箱确定性哈希（HMAC-SHA256），用于等值查询和唯一约束
    email_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)
    # bcrypt 哈希后的密码，长度 255 足够容纳 bcrypt 输出（一般 60 字符左右）
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # 默认角色（admin/agent），实际租户内角色以 TenantMember.role 为准，这里是全局默认值
    role_default: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
    # 连续登录失败次数，用于触发账号锁定逻辑
    login_fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 锁定截止时间，为 None 表示未锁定；登录失败次数超限时写入未来时间点
    locked_until: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    # 微信 openid（PRD §3.1 扫码登录，P3-11 静态加密存储）：AES-GCM 密文不确定，
    # 唯一性与等值查询走 wechat_openid_hash（HMAC-SHA256 确定性哈希）
    wechat_openid: Mapped[Optional[str]] = mapped_column(EncryptedString(255), nullable=True)
    wechat_openid_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)
    # 邮箱注册账号必须激活后才能登录（PRD §3.1.1 验证链接）
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    @validates("phone")
    def _auto_set_phone_hash(self, key, value):
        """设置 phone 时自动计算 phone_hash，保证两列同步。

        触发时机: ORM 层赋值（User(phone=...) 或 user.phone = ...），
        在 SQLAlchemy process_bind_param（加密）之前执行，此时 value 还是明文。
        value 为 None 时不设 hash（邮箱注册用户无手机号）。
        """
        if value:
            self.phone_hash = phone_hash(value)
        return value

    @validates("email")
    def _auto_set_email_hash(self, key, value):
        """设置 email 时自动计算 email_hash，保证两列同步。

        与 phone 同理: AES-GCM 密文不确定，等值查询/唯一约束走 email_hash。
        value 为 None 时不设 hash。
        """
        if value:
            self.email_hash = pii_hash(value)
        return value

    @validates("wechat_openid")
    def _auto_set_openid_hash(self, key, value):
        """设置 wechat_openid 时自动计算 wechat_openid_hash，保证两列同步（同 phone/email 模式）。"""
        if value:
            self.wechat_openid_hash = pii_hash(value)
        return value
