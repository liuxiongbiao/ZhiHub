"""add refund review history and refund outbox

Revision ID: d25_refund_outbox_review_history
Revises: d24_widget_channel
Create Date: 2026-08-12

P1-1: refund_approvals.review_history（只追加审批链，禁止覆写）
P1-2: refund_outbox（外部退款与 DB 事务解耦，支持重试与对账）
"""

from alembic import op
import sqlalchemy as sa


revision = "d25_refund_outbox_review_history"
down_revision = "d24_widget_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("refund_approvals", sa.Column("review_history", sa.Text(), nullable=True))

    op.create_table(
        "refund_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("approval_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("trade_no", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["approval_id"], ["refund_approvals.id"], name="fk_refund_outbox_approval_id"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_refund_outbox_order_id"),
    )


def downgrade() -> None:
    op.drop_table("refund_outbox")
    op.drop_column("refund_approvals", "review_history")
