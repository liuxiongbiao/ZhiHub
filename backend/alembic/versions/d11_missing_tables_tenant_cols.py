"""d11_missing_tables_tenant_cols

Revision ID: d11_missing_cols
Revises: 36e0613c0a65
Create Date: 2026-08-10

补齐从零建库缺口(012-alembic-from-scratch-fix):
  - 建 7 张无建表迁移的业务表:quick_replies / refund_approvals / billing_invoices /
    csat_evaluations / usage_records / answer_feedback / security_incidents
    (原靠 init_db create_all 兜底,alembic 链未覆盖,从零 upgrade 后缺表)
  - tenants 补 4 列:widget_theme_color / widget_logo_url / widget_welcome_text /
    default_model(模型有、迁移无)。NOT NULL 列带 server_default 兼容已有行(同 d8 wizard_step 模式)

down_revision = merge head(36e0613c0a65),本迁移为新 head。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d11_missing_cols"
down_revision: str | None = "36e0613c0a65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 7 张业务表(列定义对齐 app/models)───

    # quick_replies: 坐席快捷回复模板(022-quick-reply)
    op.create_table(
        "quick_replies",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("shortcut", sa.String(length=50), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quick_replies_tenant_id", "quick_replies", ["tenant_id"])

    # refund_approvals: 退款审批流水(004-ticket-order),client_request_id 幂等唯一
    op.create_table(
        "refund_approvals",
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("client_request_id", sa.String(length=100), nullable=False),
        sa.Column("approver_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refund_approvals_order_id", "refund_approvals", ["order_id"])
    op.create_index(
        "ix_refund_approvals_client_request_id", "refund_approvals", ["client_request_id"], unique=True
    )

    # billing_invoices: 月度账单(005-billing-csat),payment_trade_no 唯一
    op.create_table(
        "billing_invoices",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("base_fee", sa.Float(), nullable=False),
        sa.Column("overage_fee", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("payment_trade_no", sa.String(length=100), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_billing_invoices_tenant_id", "billing_invoices", ["tenant_id"])
    op.create_index(
        "ix_billing_invoices_payment_trade_no", "billing_invoices", ["payment_trade_no"], unique=True
    )

    # csat_evaluations: 满意度评价(007-session-lifecycle),无 tenant_id(经 session 间接隔离)
    op.create_table(
        "csat_evaluations",
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("feedback", sa.String(length=10), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_csat_evaluations_session_id", "csat_evaluations", ["session_id"])

    # usage_records: 按天聚合用量(005-billing-csat)
    op.create_table(
        "usage_records",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("conversation_count", sa.Integer(), nullable=False),
        sa.Column("is_overage", sa.Boolean(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_records_tenant_id", "usage_records", ["tenant_id"])

    # answer_feedback: 单条消息回答反馈(021-answer-feedback),无 tenant_id(经 message 间接隔离)
    op.create_table(
        "answer_feedback",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("bad_case", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_answer_feedback_message_id", "answer_feedback", ["message_id"])

    # security_incidents: Prompt 注入拦截记录(006-mcp-security)
    op.create_table(
        "security_incidents",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=True),
        sa.Column("user_message_preview", sa.String(length=500), nullable=False),
        sa.Column("rule_hit", sa.String(length=100), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("action_taken", sa.String(length=20), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_incidents_tenant_id", "security_incidents", ["tenant_id"])

    # ── tenants 补 4 列(020 widget 品牌定制 + 026 默认模型)───
    # NOT NULL 列带 server_default 兼容已有行(同 d8 wizard_step 模式)
    op.add_column(
        "tenants",
        sa.Column("widget_theme_color", sa.String(length=20), nullable=False, server_default="#1890ff"),
    )
    op.add_column("tenants", sa.Column("widget_logo_url", sa.String(length=500), nullable=True))
    op.add_column("tenants", sa.Column("widget_welcome_text", sa.String(length=500), nullable=True))
    op.add_column(
        "tenants",
        sa.Column(
            "default_model",
            sa.String(length=100),
            nullable=False,
            server_default="deepseek/deepseek-chat",
        ),
    )

    # ── sessions 补 end_user_id 列(009 长期记忆修复:访客 ID,按用户隔离长期记忆)───
    # d4 建表时无此列,模型后续新增但未补迁移
    op.add_column("sessions", sa.Column("end_user_id", sa.String(length=64), nullable=True))
    op.create_index("ix_sessions_end_user_id", "sessions", ["end_user_id"])


def downgrade() -> None:
    # sessions 补列
    op.drop_index("ix_sessions_end_user_id", table_name="sessions")
    op.drop_column("sessions", "end_user_id")

    # tenants 4 列
    op.drop_column("tenants", "default_model")
    op.drop_column("tenants", "widget_welcome_text")
    op.drop_column("tenants", "widget_logo_url")
    op.drop_column("tenants", "widget_theme_color")

    # 7 表(逆序)
    op.drop_index("ix_security_incidents_tenant_id", table_name="security_incidents")
    op.drop_table("security_incidents")

    op.drop_index("ix_answer_feedback_message_id", table_name="answer_feedback")
    op.drop_table("answer_feedback")

    op.drop_index("ix_usage_records_tenant_id", table_name="usage_records")
    op.drop_table("usage_records")

    op.drop_index("ix_csat_evaluations_session_id", table_name="csat_evaluations")
    op.drop_table("csat_evaluations")

    op.drop_index("ix_billing_invoices_payment_trade_no", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_tenant_id", table_name="billing_invoices")
    op.drop_table("billing_invoices")

    op.drop_index("ix_refund_approvals_client_request_id", table_name="refund_approvals")
    op.drop_index("ix_refund_approvals_order_id", table_name="refund_approvals")
    op.drop_table("refund_approvals")

    op.drop_index("ix_quick_replies_tenant_id", table_name="quick_replies")
    op.drop_table("quick_replies")
