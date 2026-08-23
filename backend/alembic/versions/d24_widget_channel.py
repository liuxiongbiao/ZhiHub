"""add widget api key hash and allowed domains to tenants

Revision ID: d24_widget_channel
Revises: d23_notifications_server_default
Create Date: 2026-08-12

规格 014 Widget 公共通道：租户级 API Key（仅存 SHA-256 哈希）与域名白名单。
"""

from alembic import op
import sqlalchemy as sa


revision = "d24_widget_channel"
down_revision = "d23_notifications_server_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("widget_api_key_hash", sa.String(64), nullable=True))
    op.add_column("tenants", sa.Column("widget_allowed_domains", sa.String(1000), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "widget_allowed_domains")
    op.drop_column("tenants", "widget_api_key_hash")
