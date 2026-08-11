"""day4_session_message

Revision ID: d4_session_msg
Revises: d3_knowledge
Create Date: 2026-08-04

新增 sessions 和 messages 表
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func

revision: str = "d4_session_msg"
down_revision: str | None = "d3_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # sessions 表
    op.create_table(
        "sessions",
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("tenant_id", BigInteger, nullable=False, index=True),
        Column("status", String(20), nullable=False, server_default="created"),
        Column("intent", String(30), nullable=True),
        Column("confidence", Float, nullable=True),
        Column("sentiment", String(20), nullable=True),
        Column("assignee_id", BigInteger, nullable=True),
        Column("created_at", DateTime, nullable=False, server_default=func.now()),
        Column("updated_at", DateTime, nullable=False, server_default=func.now()),
    )

    # messages 表
    op.create_table(
        "messages",
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("session_id", BigInteger, ForeignKey("sessions.id"), nullable=False, index=True),
        Column("role", String(20), nullable=False),
        Column("content", Text, nullable=False),
        Column("sources", JSON, nullable=True),
        Column("metadata", JSON, nullable=True),
        Column("created_at", DateTime, nullable=False, server_default=func.now()),
        Column("updated_at", DateTime, nullable=False, server_default=func.now()),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("sessions")
