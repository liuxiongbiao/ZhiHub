"""资金字段 Float -> Numeric(10,2)

Revision ID: d20_money_fields_numeric
Revises: d19_add_foreign_keys
Create Date: 2026-08-11

T-遗留1: 资金字段全部改 Numeric(10,2) 防浮点精度（金额相等比较/累加误差）。
涉及: orders.amount/paid_amount、refund_approvals.amount、
      billing_invoices.base_fee/overage_fee。
安全: Float -> Numeric 列转换在 MySQL 直接 ALTER 即可，无数据丢失
      （原值在 10^8 量级内 float 表示精确到分）。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d20_money_fields_numeric"
down_revision = "d19_add_foreign_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("orders", "amount", type_=sa.Numeric(10, 2), existing_type=sa.Float())
    op.alter_column("orders", "paid_amount", type_=sa.Numeric(10, 2), existing_type=sa.Float())
    op.alter_column("refund_approvals", "amount", type_=sa.Numeric(10, 2), existing_type=sa.Float())
    op.alter_column("billing_invoices", "base_fee", type_=sa.Numeric(10, 2), existing_type=sa.Float())
    op.alter_column("billing_invoices", "overage_fee", type_=sa.Numeric(10, 2), existing_type=sa.Float())


def downgrade() -> None:
    op.alter_column("billing_invoices", "overage_fee", type_=sa.Float(), existing_type=sa.Numeric(10, 2))
    op.alter_column("billing_invoices", "base_fee", type_=sa.Float(), existing_type=sa.Numeric(10, 2))
    op.alter_column("refund_approvals", "amount", type_=sa.Float(), existing_type=sa.Numeric(10, 2))
    op.alter_column("orders", "paid_amount", type_=sa.Float(), existing_type=sa.Numeric(10, 2))
    op.alter_column("orders", "amount", type_=sa.Float(), existing_type=sa.Numeric(10, 2))
