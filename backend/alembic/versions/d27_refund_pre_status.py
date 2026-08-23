"""add refund_approvals.pre_status ( precise state restore)

Revision ID: d27_refund_pre_status
Revises: d26_email_verification
Create Date: 2026-08-12

: 记录退款申请前的订单状态，审批拒绝/撤销/超时取消时精确回退
（已发货订单拒绝退款后回到 shipped 而非 paid）。
"""

from alembic import op
import sqlalchemy as sa


revision = "d27_refund_pre_status"
down_revision = "d26_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "refund_approvals",
        sa.Column("pre_status", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refund_approvals", "pre_status")
