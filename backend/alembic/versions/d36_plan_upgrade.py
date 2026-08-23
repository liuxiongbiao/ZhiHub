"""d36：套餐升级待议价状态。

职责: 为企业版升级保存待人工议价目标，议价期间继续沿用当前套餐服务。
"""

from alembic import op
import sqlalchemy as sa


revision = "d36_plan_upgrade"
down_revision = "3265167fe1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增待变更套餐字段。"""

    op.add_column(
        "tenants",
        sa.Column("pending_plan_type", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    """移除待变更套餐字段。"""

    op.drop_column("tenants", "pending_plan_type")
