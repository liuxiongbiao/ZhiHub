"""d4b_ticket_order_base

Revision ID: d4b_ticket_order_base
Revises: d4_session_msg
Create Date: 2026-08-10

建 tickets / orders 基表(012-alembic-from-scratch-fix)。

为什么需要: 原迁移链 d5 直接给 tickets/orders 加 phone 列,但从未建这两张表(靠
init_db create_all 兜底),导致从零 `alembic upgrade head` 在 d5 崩溃
(Table 'tickets' doesn't exist)。本迁移在 d4 与 d5 之间插入建表,使链线性可跑。
基表不含 phone/category -- phone 由 d5 加、category 由 d6 加,保留原迁移语义不变。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d4b_ticket_order_base"
down_revision: str | None = "d4_session_msg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # tickets 基表(不含 phone/category,d5/d6 增补)
    op.create_table(
        "tickets",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_no", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tickets_tenant_id", "tickets", ["tenant_id"])
    op.create_index("ix_tickets_ticket_no", "tickets", ["ticket_no"], unique=True)

    # orders 基表(不含 phone,d5 增补)
    op.create_table(
        "orders",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_no", sa.String(length=30), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("paid_amount", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_orders_tenant_id", "orders", ["tenant_id"])
    op.create_index("ix_orders_order_no", "orders", ["order_no"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_orders_order_no", table_name="orders")
    op.drop_index("ix_orders_tenant_id", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_tickets_ticket_no", table_name="tickets")
    op.drop_index("ix_tickets_tenant_id", table_name="tickets")
    op.drop_table("tickets")
