"""d29_created_by_columns

Revision ID: d29_created_by_columns
Revises: d28_billing_resolved_pii
Create Date: 2026-08-13

PRD 3.5 界面要求：工单/订单区分「Agent 创建 vs 坐席/后台创建」。
tickets/orders 新增 created_by 枚举列（agent=智能体创建 / staff=坐席后台创建 /
message=无坐席留言自动建单），存量数据回填 staff。
"""

from alembic import op
import sqlalchemy as sa


revision = "d29_created_by_columns"
down_revision = "d28_billing_resolved_pii"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("created_by", sa.String(20), nullable=False, server_default="staff"),
    )
    op.add_column(
        "orders",
        sa.Column("created_by", sa.String(20), nullable=False, server_default="staff"),
    )


def downgrade() -> None:
    op.drop_column("orders", "created_by")
    op.drop_column("tickets", "created_by")
