"""orders 增加 payment_trade_no 列

Revision ID: d18_order_payment_trade_no
Revises: d17_refund_idempotency_composite
Create Date: 2026-08-10

T8: 订单绑定自身支付流水，退款走订单自己的 trade_no（防退错账）。
demo 无真实支付流程，新列恒 NULL；真实接入后订单支付时回填。
nullable 无 server_default 无妨（新列全部为 NULL）。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d18_order_payment_trade_no"
down_revision = "d17_refund_idempotency_composite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("payment_trade_no", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "payment_trade_no")
