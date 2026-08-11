"""refund_approvals 幂等约束改复合粒度

Revision ID: d17_refund_idempotency_composite
Revises: d16_unique_constraints
Create Date: 2026-08-10

T6: client_request_id 全局唯一 -> (order_id, client_request_id) 复合唯一。
背景: 全局唯一键跨租户碰撞——租户 B 用与租户 A 相同的 client_request_id 申请退款时
      撞唯一约束导致功能 DoS（Redis 幂等 key 已同步改为 order_id 粒度，见 core/idempotency.py）。
安全: 原约束保证 client_request_id 全局唯一，故复合键必然无重复数据，无需清理。
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "d17_refund_idempotency_composite"
down_revision = "d16_unique_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 旧: client_request_id 全局唯一索引（d11 建）
    op.drop_index("ix_refund_approvals_client_request_id", table_name="refund_approvals")
    # 新: (order_id, client_request_id) 复合唯一，与 Redis 幂等 key 粒度一致
    op.create_index(
        "uq_refund_approval_order_client", "refund_approvals",
        ["order_id", "client_request_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_refund_approval_order_client", table_name="refund_approvals")
    op.create_index(
        "ix_refund_approvals_client_request_id", "refund_approvals",
        ["client_request_id"], unique=True,
    )
