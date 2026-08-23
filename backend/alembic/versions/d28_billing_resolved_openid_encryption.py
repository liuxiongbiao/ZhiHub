"""d28_billing_resolved_openid_encryption

Revision ID: d28_billing_resolved_pii
Revises: d27_refund_pre_status
Create Date: 2026-08-13

 计费口径（PRD 3.4/3.9）:
  sessions 表新增 effective_reply / billed 两列——会话进入 RESOLVED 且产生有效回复
  才计费，billed 防止重复计费。

P3-11 身份凭证加密（PRD 4.3）:
  users.wechat_openid 改为 AES-GCM 静态加密存储（长度放宽至 255），
  新增 wechat_openid_hash（HMAC-SHA256）承载唯一性与等值查询；
  存量明文 openid 在本迁移中加密回填。
"""

from alembic import op
import sqlalchemy as sa


revision = "d28_billing_resolved_pii"
down_revision = "d27_refund_pre_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 会话计费状态列
    op.add_column(
        "sessions",
        sa.Column("effective_reply", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "sessions",
        sa.Column("billed", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    # P3-11: openid 加密存储（新增哈希列 → 回填 → 收紧索引）
    op.add_column("users", sa.Column("wechat_openid_hash", sa.String(64), nullable=True))
    op.create_index("ix_users_wechat_openid_hash", "users", ["wechat_openid_hash"], unique=True)

    from app.core.crypto import encrypt, pii_hash

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, wechat_openid FROM users WHERE wechat_openid IS NOT NULL")
    ).fetchall()
    for row_id, openid in rows:
        bind.execute(
            sa.text("UPDATE users SET wechat_openid=:enc, wechat_openid_hash=:h WHERE id=:id"),
            {"enc": encrypt(openid), "h": pii_hash(openid), "id": row_id},
        )

    op.drop_index("ix_users_wechat_openid", table_name="users")
    op.alter_column(
        "users",
        "wechat_openid",
        existing_type=sa.String(128),
        type_=sa.String(255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "wechat_openid",
        existing_type=sa.String(255),
        type_=sa.String(128),
        existing_nullable=True,
    )
    op.create_index("ix_users_wechat_openid", "users", ["wechat_openid"], unique=True)
    op.drop_index("ix_users_wechat_openid_hash", table_name="users")
    op.drop_column("users", "wechat_openid_hash")
    op.drop_column("sessions", "billed")
    op.drop_column("sessions", "effective_reply")
