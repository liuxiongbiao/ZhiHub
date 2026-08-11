"""
backend/app/crud/order.py

职责: orders / refund_approvals 表的数据库访问层（订单查询、乐观锁更新、退款审批）
对应日程: 004-ticket-order

核心设计:
  订单查询强制 tenant_id 过滤（多租户隔离）；乐观锁 UPDATE ... WHERE version=expected 防并发覆写。
  退款审批表无 tenant_id 列，需通过 order_id 关联订单间接隔离（见下方 BUG 标注）。
"""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.refund_approval import RefundApproval


def get_by_order_no(db: Session, tenant_id: int, order_no: str) -> Order | None:
    """按 tenant_id + order_no 查询订单（双因子校验入口）。"""
    return db.execute(
        select(Order).where(Order.tenant_id == tenant_id, Order.order_no == order_no)
    ).scalar_one_or_none()


def get_by_id(db: Session, order_id: int, tenant_id: int) -> Order | None:
    """按 tenant_id + 订单主键查询。"""
    return db.execute(
        select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id)
    ).scalar_one_or_none()


def list_orders(db: Session, tenant_id: int, *, page: int = 1, page_size: int = 20) -> tuple[list[Order], int]:
    """分页查询租户订单列表（按创建时间倒序）。返回 (items, total)。"""
    total = db.execute(
        select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id)
    ).scalar() or 0
    items = db.execute(
        select(Order).where(Order.tenant_id == tenant_id).order_by(Order.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return list(items), total


def update_status_optimistic(db: Session, order: Order, new_status: str) -> bool:
    """乐观锁更新: UPDATE ... WHERE id=:id AND version=:expected_version.
    成功返回 True, 版本冲突返回 False."""
    expected_version = order.version
    result = db.execute(
        update(Order)
        .where(Order.id == order.id, Order.version == expected_version)
        .values(status=new_status, version=expected_version + 1)
    )
    if result.rowcount == 0:
        return False
    # T26: 内存对象与 DB 同步——Core update 绕过 ORM，status 需手动赋值，
    # 否则同事务内再读 order.status 得到旧值（隐雷）
    order.status = new_status
    db.expire(order, ["version"])
    db.refresh(order, ["version"])
    return True


def create_refund_approval(
    db: Session, *, order_id: int, amount: float, reason: str | None = None, client_request_id: str,
) -> RefundApproval:
    """创建退款审批记录（client_request_id 作幂等键）。"""
    approval = RefundApproval(order_id=order_id, amount=amount, reason=reason, client_request_id=client_request_id)
    db.add(approval)
    db.flush()
    return approval


def get_refund_by_request_id(db: Session, client_request_id: str) -> RefundApproval | None:
    """按幂等键 client_request_id 查询退款审批（防重复提交）。"""
    return db.execute(
        select(RefundApproval).where(RefundApproval.client_request_id == client_request_id)
    ).scalar_one_or_none()


def get_refund_approval(db: Session, approval_id: int, tenant_id: int) -> RefundApproval | None:
    """按主键 + tenant_id 查询退款审批（防御纵深：经关联订单校验归属）。"""
    return db.execute(
        select(RefundApproval)
        .join(Order, Order.id == RefundApproval.order_id)
        .where(RefundApproval.id == approval_id, Order.tenant_id == tenant_id)
    ).scalar_one_or_none()


def list_refund_approvals(db: Session, tenant_id: int, *, page: int = 1, page_size: int = 20) -> tuple[list[RefundApproval], int]:
    """分页查询退款审批列表。

    """
    total = db.execute(
        select(func.count()).select_from(RefundApproval)
        .join(Order, Order.id == RefundApproval.order_id)
        .where(Order.tenant_id == tenant_id)
    ).scalar() or 0
    items = db.execute(
        select(RefundApproval).join(Order, Order.id == RefundApproval.order_id)
        .where(Order.tenant_id == tenant_id)
        .order_by(RefundApproval.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return list(items), total
