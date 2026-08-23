"""业务表补 ForeignKey 约束

Revision ID: d19_add_foreign_keys
Revises: d18_order_payment_trade_no
Create Date: 2026-08-10

-A: 强关联列补 FK（此前 8+ 处无约束，孤儿数据无兜底）。
不设 ON DELETE（项目无级联删除业务，默认 RESTRICT 防误删被引用行）。

安全: 加 FK 前逐表检查孤儿数据（LEFT JOIN 无匹配），存在则中止迁移
（提示先清理），避免"加约束失败/遗留孤儿"。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d19_add_foreign_keys"
down_revision = "d18_order_payment_trade_no"
branch_labels = None
depends_on = None


def _assert_no_orphans(table: str, col: str, ref_table: str) -> None:
    """校验表中无孤儿引用（col 非 NULL 且 ref_table.id 不存在）。有孤儿则中止迁移。"""
    sql = sa.text(
        f"SELECT COUNT(*) FROM {table} t "
        f"LEFT JOIN {ref_table} r ON t.{col} = r.id "
        f"WHERE t.{col} IS NOT NULL AND r.id IS NULL"
    )
    n = op.get_bind().execute(sql).scalar()
    assert n == 0, (
        f"迁移中止: {table}.{col} 存在 {n} 条孤儿数据（引用 {ref_table} 中不存在的 id），"
        "请先清理再执行迁移"
    )


def upgrade() -> None:
    # 孤儿数据检查（fail-safe）
    _assert_no_orphans("refund_approvals", "order_id", "orders")
    _assert_no_orphans("refund_approvals", "approver_id", "users")
    _assert_no_orphans("tickets", "assignee_id", "users")
    _assert_no_orphans("sessions", "assignee_id", "users")
    _assert_no_orphans("csat_evaluations", "session_id", "sessions")
    _assert_no_orphans("csat_evaluations", "message_id", "messages")
    _assert_no_orphans("answer_feedback", "message_id", "messages")
    _assert_no_orphans("security_incidents", "session_id", "sessions")
    _assert_no_orphans("audit_logs", "user_id", "users")

    # 加 FK（不设 ON DELETE，默认 RESTRICT）
    op.create_foreign_key("fk_refund_approvals_order_id", "refund_approvals", "orders", ["order_id"], ["id"])
    op.create_foreign_key("fk_refund_approvals_approver_id", "refund_approvals", "users", ["approver_id"], ["id"])
    op.create_foreign_key("fk_tickets_assignee_id", "tickets", "users", ["assignee_id"], ["id"])
    op.create_foreign_key("fk_sessions_assignee_id", "sessions", "users", ["assignee_id"], ["id"])
    op.create_foreign_key("fk_csat_session_id", "csat_evaluations", "sessions", ["session_id"], ["id"])
    op.create_foreign_key("fk_csat_message_id", "csat_evaluations", "messages", ["message_id"], ["id"])
    op.create_foreign_key("fk_answer_feedback_message_id", "answer_feedback", "messages", ["message_id"], ["id"])
    op.create_foreign_key("fk_security_incidents_session_id", "security_incidents", "sessions", ["session_id"], ["id"])
    op.create_foreign_key("fk_audit_logs_user_id", "audit_logs", "users", ["user_id"], ["id"])


def downgrade() -> None:
    # MySQL 的 drop_constraint 必须带 table_name
    op.drop_constraint("fk_audit_logs_user_id", "audit_logs", type_="foreignkey")
    op.drop_constraint("fk_security_incidents_session_id", "security_incidents", type_="foreignkey")
    op.drop_constraint("fk_answer_feedback_message_id", "answer_feedback", type_="foreignkey")
    op.drop_constraint("fk_csat_message_id", "csat_evaluations", type_="foreignkey")
    op.drop_constraint("fk_csat_session_id", "csat_evaluations", type_="foreignkey")
    op.drop_constraint("fk_sessions_assignee_id", "sessions", type_="foreignkey")
    op.drop_constraint("fk_tickets_assignee_id", "tickets", type_="foreignkey")
    op.drop_constraint("fk_refund_approvals_approver_id", "refund_approvals", type_="foreignkey")
    op.drop_constraint("fk_refund_approvals_order_id", "refund_approvals", type_="foreignkey")
