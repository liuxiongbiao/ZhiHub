"""day7_knowledge_lifecycle

Revision ID: d7_knowledge
Revises: d6_ticket_cat
Create Date: 2026-08-06

给 knowledge_documents 加 expires_at + quality_status 列（010 业务闭环 US3 知识库生命周期）。
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import Column, DateTime, String

revision: str = "d7_knowledge"
down_revision: str | None = "d6_ticket_cat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_documents", Column("expires_at", DateTime, nullable=True))
    op.add_column("knowledge_documents", Column("quality_status", String(20), nullable=False, server_default="ready"))


def downgrade() -> None:
    op.drop_column("knowledge_documents", "quality_status")
    op.drop_column("knowledge_documents", "expires_at")