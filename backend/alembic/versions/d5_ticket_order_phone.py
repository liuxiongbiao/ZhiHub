"""day5_ticket_order_phone

Revision ID: d5_ticket_phone
Revises: d4b_ticket_order_base
Create Date: 2026-08-05

给 tickets 和 orders 表加 phone 列（规格 006 US1 双因子校验：
终端用户查询工单/订单前校验手机号后4位与业务数据绑定手机号匹配）。
nullable=True：向后兼容已有数据，未绑定手机号的记录视为不可查询（防枚举）。

down_revision 从 d4_session_msg 改接 d4b_ticket_order_base（规格 012）：
d4b 已建出 tickets/orders 基表，本迁移方可对其加 phone 列，否则从零 upgrade 崩溃。
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, String

revision: str = "d5_ticket_phone"
down_revision: str | None = "d4b_ticket_order_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # tickets.phone：终端用户绑定手机号
    op.add_column("tickets", Column("phone", String(20), nullable=True))
    op.create_index("ix_tickets_phone", "tickets", ["phone"])

    # orders.phone：终端用户绑定手机号
    op.add_column("orders", Column("phone", String(20), nullable=True))
    op.create_index("ix_orders_phone", "orders", ["phone"])


def downgrade() -> None:
    op.drop_index("ix_orders_phone", table_name="orders")
    op.drop_column("orders", "phone")
    op.drop_index("ix_tickets_phone", table_name="tickets")
    op.drop_column("tickets", "phone")