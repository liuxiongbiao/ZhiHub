"""d34: tenants.is_demo 持久演示标记

演示租户所有权不能只靠可碰撞的 name 判断；is_demo 布尔列提供持久、不可误判的标记。
归档旧演示实例时仅改 status/name，is_demo 保留 True 以识别历史演示租户。
"""

from alembic import op
import sqlalchemy as sa

revision = "d34"
down_revision = "d33_refund_event_db_immutability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("tenants", "is_demo")
