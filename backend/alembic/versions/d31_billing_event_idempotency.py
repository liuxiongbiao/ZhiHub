"""会话计费事件幂等与每日聚合快照位点

Revision ID: d31_billing_event_idempotency
Revises: d30_refund_approval_safety
Create Date: 2026-08-16

批次 2:
  - billing_events 以 session_id/event_key 唯一约束承载会话计费事件；
  - usage_records.snapshot_total 保存数据库已提交的 Redis 累计位点。
"""

from alembic import op
import sqlalchemy as sa


revision = "d31_billing_event_idempotency"
down_revision = "d30_refund_approval_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("usage_amount", sa.Numeric(16, 6), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_billing_events_session_id",
        ),
        sa.UniqueConstraint("session_id", name="uq_billing_events_session_id"),
        sa.UniqueConstraint("event_key", name="uq_billing_events_event_key"),
    )
    op.create_index("ix_billing_events_tenant_id", "billing_events", ["tenant_id"])
    op.add_column(
        "usage_records",
        sa.Column(
            "snapshot_total",
            sa.Numeric(16, 6),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("usage_records", "snapshot_total")
    op.drop_index("ix_billing_events_tenant_id", table_name="billing_events")
    op.drop_table("billing_events")
