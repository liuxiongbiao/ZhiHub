"""
backend/app/models/refund_approval.py

职责: refund_approvals 表 ORM 模型定义（退款审批记录，只追加不覆写）

核心设计:
  退款审批流水，规则定稿禁止 UPDATE/DELETE，靠 client_request_id 做幂等去重。
  关键字段: order_id(关联订单)、amount(退款金额)、status(pending/approved/rejected)、
            client_request_id(幂等键，客户端唯一请求 ID)、approver_id(审批人)
  幂等粒度: 复合唯一 (order_id, client_request_id) —— 同一订单的同一请求 id 只允许一条；
            跨订单/跨租户使用相同 id 互不干扰（防跨租户碰撞），与 Redis 幂等 key 粒度一致。
"""

from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class RefundApproval(BaseModel):
    __tablename__ = "refund_approvals"
    __table_args__ = (
        # 幂等去重（与 core/idempotency.py 的 key 粒度 (order_id, client_request_id) 一致）
        UniqueConstraint("order_id", "client_request_id", name="uq_refund_approval_order_client"),
    )

    # 关联订单（T27: 资金流水 FK 兜底，防孤儿数据）
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("orders.id", name="fk_refund_approvals_order_id"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # 退款金额（Numeric 防精度）
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # 退款原因
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending/approved/rejected
    # 幂等键: 客户端生成的唯一请求 ID；唯一性由 __table_args__ 复合约束
    # (order_id, client_request_id) 保证（d17 已 drop 单列索引，模型不声明 index 以对齐迁移，
    # 否则 autogenerate 持续报"缺 ix_refund_approvals_client_request_id"漂移）
    client_request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    approver_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", name="fk_refund_approvals_approver_id"), nullable=True)  # 审批人 ID
