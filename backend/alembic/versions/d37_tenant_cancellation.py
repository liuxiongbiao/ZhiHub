"""d37：租户注销 30 天导出窗口。

为注销申请、计划物理删除时间和撤销时的原状态恢复增加持久化字段。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError


revision = "d37_tenant_cancellation"
down_revision = "d36_plan_upgrade"
branch_labels = None
depends_on = None


def _replace_refund_delete_trigger(*, guarded: bool) -> None:
    """保留审批事件日常不可变约束，仅允许显式租户物理删除事务清理。"""

    if op.get_bind().dialect.name != "mysql":
        return
    try:
        op.execute("DROP TRIGGER IF EXISTS trg_refund_approval_events_no_delete")
        if guarded:
            op.execute(
                "CREATE TRIGGER trg_refund_approval_events_no_delete "
                "BEFORE DELETE ON refund_approval_events FOR EACH ROW "
                "BEGIN "
                "IF IFNULL(@zhihub_tenant_purge, 0) <> 1 THEN "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'refund approval events are immutable'; "
                "END IF; "
                "END"
            )
        else:
            op.execute(
                "CREATE TRIGGER trg_refund_approval_events_no_delete "
                "BEFORE DELETE ON refund_approval_events FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'refund approval events are immutable'"
            )
    except DBAPIError as exc:
        error_code = exc.orig.args[0] if getattr(exc.orig, "args", None) else None
        if error_code != 1419:
            raise


def upgrade() -> None:
    op.add_column("tenants", sa.Column("cancellation_requested_at", sa.DateTime(), nullable=True))
    op.add_column("tenants", sa.Column("scheduled_deletion_at", sa.DateTime(), nullable=True))
    op.add_column("tenants", sa.Column("cancellation_previous_status", sa.String(length=20), nullable=True))
    op.create_index(
        "ix_tenants_scheduled_deletion_at",
        "tenants",
        ["scheduled_deletion_at"],
        unique=False,
    )
    _replace_refund_delete_trigger(guarded=True)


def downgrade() -> None:
    _replace_refund_delete_trigger(guarded=False)
    op.drop_index("ix_tenants_scheduled_deletion_at", table_name="tenants")
    op.drop_column("tenants", "cancellation_previous_status")
    op.drop_column("tenants", "scheduled_deletion_at")
    op.drop_column("tenants", "cancellation_requested_at")
