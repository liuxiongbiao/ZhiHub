"""fix notifications created_at/updated_at server_default

Revision ID: d23_notifications_server_default
Revises: d22_notifications
Create Date: 2026-08-12

修复: d22 创建 notifications 表时漏了 server_default，
导致插入站内信报 "Field 'created_at' doesn't have a default value"。
对已应用 d22 的库执行 ALTER 补默认值（新库直接跑 d22 修复版即可）。
"""

from alembic import op
import sqlalchemy as sa


revision = "d23_notifications_server_default"
down_revision = "d22_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "notifications", "created_at",
        existing_type=sa.DateTime(), server_default=sa.text("now()"), existing_nullable=False,
    )
    op.alter_column(
        "notifications", "updated_at",
        existing_type=sa.DateTime(), server_default=sa.text("now()"), existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column("notifications", "created_at", existing_type=sa.DateTime(), server_default=None)
    op.alter_column("notifications", "updated_at", existing_type=sa.DateTime(), server_default=None)