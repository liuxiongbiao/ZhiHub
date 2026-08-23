"""day6_ticket_category

Revision ID: d6_ticket_cat
Revises: d5_ticket_phone
Create Date: 2026-08-05

给 tickets 表加 category 列（规格 007 US3 留言建单区分留言工单与正常工单）。
nullable + default "general" 保证现有工单数据兼容。
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, String

revision: str = "d6_ticket_cat"
down_revision: str | None = "d5_ticket_phone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", Column("category", String(30), nullable=True, server_default="general"))


def downgrade() -> None:
    op.drop_column("tickets", "category")