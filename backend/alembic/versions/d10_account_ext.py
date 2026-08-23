"""d10_account_ext

Revision ID: d10_account_ext
Revises: d9_pii_encrypt
Create Date: 2026-08-09

PRD 3.1 账户体系扩展（#10-#17，不含 #12 微信）:
  - users.phone / phone_hash: nullable=True（邮箱注册用户无手机号）
  - users 新增 email 列（EncryptedString=String(255)，AES-GCM 加密，nullable=True）
  - users 新增 email_hash 列（HMAC-SHA256 hex=64，unique+index，用于邮箱等值查询/唯一约束）

注意: 本迁移假设 demo 空表（无已有数据需回填 email_hash）。
wechat_openid 不在本迁移添加（规格 012 去重）：由 d10_wx_oid 统一添加
  String(128) unique 版本，与模型 user.py 最终定义一致；原 d10_account_ext 重复添加
  String(64) 非 unique 版本会导致从零 upgrade duplicate column，故移除。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d10_account_ext"
down_revision: str | None = "d9_pii_encrypt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # phone / phone_hash 改为 nullable（邮箱注册用户无手机号）
    op.alter_column("users", "phone",
                     existing_type=sa.String(255),
                     existing_nullable=False,
                     nullable=True)
    op.alter_column("users", "phone_hash",
                     existing_type=sa.String(64),
                     existing_nullable=False,
                     nullable=True)

    # 新增 email 列（AES-GCM 加密存储，与 phone 同理用 String(255) 容纳 base64 密文）
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    # 新增 email_hash 列（HMAC-SHA256 确定性哈希，unique+index 用于等值查询/唯一约束）
    op.add_column("users", sa.Column("email_hash", sa.String(64), nullable=True))
    op.create_index("ix_users_email_hash", "users", ["email_hash"], unique=True)

    # wechat_openid 不在此添加：由 d10_wx_oid 统一添加 String(128) unique 版本（见模块 docstring）


def downgrade() -> None:
    op.drop_index("ix_users_email_hash", table_name="users")
    op.drop_column("users", "email_hash")
    op.drop_column("users", "email")

    op.alter_column("users", "phone_hash",
                     existing_type=sa.String(64),
                     existing_nullable=True,
                     nullable=False)
    op.alter_column("users", "phone",
                     existing_type=sa.String(255),
                     existing_nullable=True,
                     nullable=False)
