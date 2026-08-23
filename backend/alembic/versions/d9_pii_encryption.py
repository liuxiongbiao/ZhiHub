"""d9_pii_encryption

Revision ID: d9_pii_encrypt
Revises: d8_wizard
Create Date: 2026-08-09

PRD 3.1/4.3 PII 字段静态加密：
  - users.phone: String(20) -> String(255)（容纳 AES-GCM base64 密文），移除 unique/index
  - users 新增 phone_hash 列（HMAC-SHA256 确定性哈希，unique+index，用于登录等值查询）
  - tickets.phone / orders.phone: String(20) -> String(255)，移除 index（密文不确定，索引无意义）

注意: 本迁移假设 demo 空表（无已有明文数据需批量加密）。
      若已有明文数据，需先批量加密 + 回填 phone_hash，再执行此 DDL。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d9_pii_encrypt"
down_revision: str | None = "d8_wizard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── users 表 ──
    # 新增 phone_hash 列（HMAC-SHA256 hex = 64 字符，unique+index 用于等值查询）
    op.add_column("users", sa.Column("phone_hash", sa.String(64), nullable=True))
    op.create_index("ix_users_phone_hash", "users", ["phone_hash"], unique=True)

    # phone 列扩容 String(20) → String(255)（容纳 base64 密文），移除旧 unique/index
    op.drop_index("ix_users_phone", table_name="users")
    op.alter_column("users", "phone",
                     existing_type=sa.String(20),
                     type_=sa.String(255),
                     existing_nullable=False)

    # ── tickets 表 ──
    op.drop_index("ix_tickets_phone", table_name="tickets")
    op.alter_column("tickets", "phone",
                     existing_type=sa.String(20),
                     type_=sa.String(255),
                     existing_nullable=True)

    # ── orders 表 ──
    op.drop_index("ix_orders_phone", table_name="orders")
    op.alter_column("orders", "phone",
                     existing_type=sa.String(20),
                     type_=sa.String(255),
                     existing_nullable=True)


def downgrade() -> None:
    # ── orders 表 ──
    op.alter_column("orders", "phone",
                     existing_type=sa.String(255),
                     type_=sa.String(20),
                     existing_nullable=True)
    op.create_index("ix_orders_phone", "orders", ["phone"])

    # ── tickets 表 ──
    op.alter_column("tickets", "phone",
                     existing_type=sa.String(255),
                     type_=sa.String(20),
                     existing_nullable=True)
    op.create_index("ix_tickets_phone", "tickets", ["phone"])

    # ── users 表 ──
    op.alter_column("users", "phone",
                     existing_type=sa.String(255),
                     type_=sa.String(20),
                     existing_nullable=False)
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)
    op.drop_index("ix_users_phone_hash", table_name="users")
    op.drop_column("users", "phone_hash")
