"""add users.email_verified ( email activation)

Revision ID: d26_email_verification
Revises: d25_refund_outbox_review_history
Create Date: 2026-08-12

: 邮箱注册必须激活才能登录（PRD 3.1.1）。
存量邮箱用户视为已验证，避免误锁真实账号。
"""

from alembic import op
import sqlalchemy as sa


revision = "d26_email_verification"
down_revision = "d25_refund_outbox_review_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    # 存量邮箱用户（激活能力上线前注册的账号）直接视为已验证
    op.execute(
        "UPDATE users SET email_verified = 1 WHERE email IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("users", "email_verified")
