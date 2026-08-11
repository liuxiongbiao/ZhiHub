"""add unique constraints for invoices/usage/feedback/csat

Revision ID: d16_unique_constraints
Revises: d11_missing_cols
Create Date: 2026-08-10

补 M16 缺失的唯一约束，防重复数据（已查无重复，安全添加）：
  - billing_invoices(tenant_id, period)：同租户同账期仅一张账单
  - usage_records(tenant_id, date)：同租户同天仅一条用量
  - answer_feedback(message_id)：一条消息最多一条反馈
  - csat_evaluations(session_id)：同一会话仅一条评价
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "d16_unique_constraints"
down_revision = "d11_missing_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_invoice_tenant_period", "billing_invoices", ["tenant_id", "period"])
    op.create_unique_constraint("uq_usage_tenant_date", "usage_records", ["tenant_id", "date"])
    op.create_unique_constraint("uq_answer_feedback_message", "answer_feedback", ["message_id"])
    op.create_unique_constraint("uq_csat_session", "csat_evaluations", ["session_id"])


def downgrade() -> None:
    op.drop_constraint("uq_csat_session", "csat_evaluations", type_="unique")
    op.drop_constraint("uq_answer_feedback_message", "answer_feedback", type_="unique")
    op.drop_constraint("uq_usage_tenant_date", "usage_records", type_="unique")
    op.drop_constraint("uq_invoice_tenant_period", "billing_invoices", type_="unique")
