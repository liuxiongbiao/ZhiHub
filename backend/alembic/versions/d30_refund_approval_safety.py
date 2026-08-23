"""退款待审批状态、Outbox 租约与不可变审批事件

Revision ID: d30_refund_approval_safety
Revises: d29_created_by_columns
Create Date: 2026-08-16

批次 1:
  - refund_outbox.approval_id 唯一，杜绝同一审批生成多条退款任务；
  - processing_at 提供 Worker 领取租约与超时恢复；
  - refund_approval_events 只追加审批审计事件，MySQL 触发器阻止更新/删除。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError


revision = "d30_refund_approval_safety"
down_revision = "d29_created_by_columns"
branch_labels = None
depends_on = None


_IMMUTABILITY_TRIGGERS = (
    "CREATE TRIGGER trg_refund_approval_events_no_update "
    "BEFORE UPDATE ON refund_approval_events FOR EACH ROW "
    "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'refund approval events are immutable'",
    "CREATE TRIGGER trg_refund_approval_events_no_delete "
    "BEFORE DELETE ON refund_approval_events FOR EACH ROW "
    "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'refund approval events are immutable'",
)


def _create_mysql_immutability_triggers() -> None:
    """创建数据库级只追加保护；binlog 权限不足时由 ORM 保护兜底。"""
    for statement in _IMMUTABILITY_TRIGGERS:
        try:
            op.execute(statement)
        except DBAPIError as exc:
            error_code = exc.orig.args[0] if getattr(exc.orig, "args", None) else None
            if error_code == 1419:
                return
            raise


def upgrade() -> None:
    op.add_column("refund_outbox", sa.Column("processing_at", sa.DateTime(), nullable=True))
    op.create_unique_constraint(
        "uq_refund_outbox_approval_id",
        "refund_outbox",
        ["approval_id"],
    )
    op.create_table(
        "refund_approval_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["refund_approvals.id"],
            name="fk_refund_approval_events_approval_id",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_refund_approval_events_actor_id",
        ),
    )
    op.create_index(
        "ix_refund_approval_events_approval_id",
        "refund_approval_events",
        ["approval_id"],
    )

    if op.get_bind().dialect.name == "mysql":
        _create_mysql_immutability_triggers()


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.execute("DROP TRIGGER IF EXISTS trg_refund_approval_events_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_refund_approval_events_no_update")
    op.drop_table("refund_approval_events")
    op.drop_constraint("uq_refund_outbox_approval_id", "refund_outbox", type_="unique")
    op.drop_column("refund_outbox", "processing_at")
