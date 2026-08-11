"""d10_wechat_openid

Revision ID: d10_wx_oid
Revises: d9_pii_encrypt
Create Date: 2026-08-09

PRD 3.1 微信扫码登录：users 表新增 wechat_openid 列（唯一可空）。
nullable + unique 保证：未绑定为 NULL（多行 NULL 不冲突），绑定后全表唯一。
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, String

revision: str = "d10_wx_oid"
down_revision: str | None = "d9_pii_encrypt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", Column("wechat_openid", String(128), nullable=True))
    op.create_index("ix_users_wechat_openid", "users", ["wechat_openid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_wechat_openid", table_name="users")
    op.drop_column("users", "wechat_openid")
