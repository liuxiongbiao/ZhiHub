"""day3_knowledge_document

Revision ID: d3_knowledge
Revises: fe03006827bc
Create Date: 2026-08-04

表: knowledge_documents — 知识库文档元数据
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text, func

# revision identifiers, used by Alembic.
revision: str = "d3_knowledge"
down_revision: str | None = "fe03006827bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("tenant_id", BigInteger, nullable=False, index=True),
        Column("filename", String(500), nullable=False),
        Column("file_type", String(20), nullable=False),
        Column("file_size", Integer, nullable=False),
        Column("status", String(20), nullable=False, server_default="pending"),
        Column("error_message", Text, nullable=True),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("is_latest", Boolean, nullable=False, server_default="1"),
        Column("chunk_count", Integer, nullable=False, server_default="0"),
        Column("created_at", DateTime, nullable=False, server_default=func.now()),
        Column("updated_at", DateTime, nullable=False, server_default=func.now()),
    )


def downgrade() -> None:
    op.drop_table("knowledge_documents")
