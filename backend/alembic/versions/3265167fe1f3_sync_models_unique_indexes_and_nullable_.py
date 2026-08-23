"""
d35: 模型与迁移一致性修复（alembic check 首次运行暴露的漂移）

职责: 补齐历史遗留的模型-迁移漂移，使 `alembic check` 通过:
  - answer_feedback.message_id / csat_evaluations.session_id:
      唯一约束 + 普通索引 -> 合并为唯一索引（与 ORM 模型 unique=True, index=True 一致）
  - billing_invoices.base_fee/overage_fee、orders.amount/paid_amount、refund_approvals.amount:
      补 NOT NULL（模型 nullable=False，此前迁移漏声明）

Revision ID: 3265167fe1f3
Revises: d34
Create Date: 2026-08-23 20:28:49.067306
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '3265167fe1f3'
down_revision: Union[str, None] = 'd34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # answer_feedback.message_id / csat_evaluations.session_id:
    #   外键依赖现有普通索引，MySQL 不允许直接删除——先改名腾出名字，再建唯一索引，最后删旧索引
    op.execute(
        "ALTER TABLE answer_feedback RENAME INDEX ix_answer_feedback_message_id "
        "TO ix_answer_feedback_message_id_legacy"
    )
    op.drop_index("uq_answer_feedback_message", table_name="answer_feedback")
    op.create_index("ix_answer_feedback_message_id", "answer_feedback", ["message_id"], unique=True)
    op.drop_index("ix_answer_feedback_message_id_legacy", table_name="answer_feedback")

    op.execute(
        "ALTER TABLE csat_evaluations RENAME INDEX ix_csat_evaluations_session_id "
        "TO ix_csat_evaluations_session_id_legacy"
    )
    op.drop_index("uq_csat_session", table_name="csat_evaluations")
    op.create_index("ix_csat_evaluations_session_id", "csat_evaluations", ["session_id"], unique=True)
    op.drop_index("ix_csat_evaluations_session_id_legacy", table_name="csat_evaluations")

    # 金额字段补 NOT NULL（模型 nullable=False，此前迁移漏声明）
    op.alter_column('billing_invoices', 'base_fee',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=False)
    op.alter_column('billing_invoices', 'overage_fee',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=False)
    op.alter_column('orders', 'amount',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=False)
    op.alter_column('orders', 'paid_amount',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=False)
    op.alter_column('refund_approvals', 'amount',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=False)


def downgrade() -> None:
    # 回滚金额字段
    op.alter_column('refund_approvals', 'amount',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=True)
    op.alter_column('orders', 'paid_amount',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=True)
    op.alter_column('orders', 'amount',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=True)
    op.alter_column('billing_invoices', 'overage_fee',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=True)
    op.alter_column('billing_invoices', 'base_fee',
                    existing_type=mysql.DECIMAL(precision=10, scale=2),
                    nullable=True)

    # 回滚索引：恢复"唯一约束 + 普通索引"原状
    op.execute(
        "ALTER TABLE csat_evaluations RENAME INDEX ix_csat_evaluations_session_id "
        "TO ix_csat_evaluations_session_id_legacy"
    )
    op.create_index("ix_csat_evaluations_session_id", "csat_evaluations", ["session_id"], unique=False)
    op.create_index("uq_csat_session", "csat_evaluations", ["session_id"], unique=True)
    op.drop_index("ix_csat_evaluations_session_id_legacy", table_name="csat_evaluations")

    op.execute(
        "ALTER TABLE answer_feedback RENAME INDEX ix_answer_feedback_message_id "
        "TO ix_answer_feedback_message_id_legacy"
    )
    op.create_index("ix_answer_feedback_message_id", "answer_feedback", ["message_id"], unique=False)
    op.create_index("uq_answer_feedback_message", "answer_feedback", ["message_id"], unique=True)
    op.drop_index("ix_answer_feedback_message_id_legacy", table_name="answer_feedback")
