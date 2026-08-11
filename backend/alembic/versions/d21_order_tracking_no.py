"""add tracking_no to orders (013 US6 订单发货)

Revision ID: d21_order_tracking_no
Revises: d20_money_fields_numeric
Create Date: 2026-08-11

订单发货（paid -> shipped）需记录物流单号；模型已定义 tracking_no 列，
此迁移将列同步到数据库（修复订单列表 500：Unknown column 'orders.tracking_no'）。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d21_order_tracking_no"
down_revision = "d20_money_fields_numeric"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("tracking_no", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "tracking_no")
