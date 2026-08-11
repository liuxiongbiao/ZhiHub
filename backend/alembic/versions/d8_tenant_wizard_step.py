"""day8_tenant_wizard_step

Revision ID: d8_wizard
Revises: d7_knowledge
Create Date: 2026-08-09

给 tenants 表加 wizard_step 列（PRD 3.1 配置向导：basic->knowledge->mcp->team->done）。
nullable=False + server_default="basic" 保证现有租户数据兼容。
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, String

revision: str = "d8_wizard"
down_revision: str | None = "d7_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        Column("wizard_step", String(20), nullable=False, server_default="basic"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "wizard_step")
