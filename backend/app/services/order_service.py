"""
backend/app/services/order_service.py

职责: 【资金安全核心】订单状态机跃迁校验 + 乐观锁 + 退款金额上限校验 + 大额退款分级审批
分工: 🔴 强模型独立完成（状态机漏一个分支=可被越权跳转的资金漏洞）
对应日程: Day 5

核心设计:
  退款是资金逆流操作，本模块用"三道防线 + 分级审批"兜底：
  1) 幂等键拦截重复退款请求（client_request_id 维度）；
  2) 退款金额不得超过订单实付金额（paid_amount 上限）；
  3) 状态机 + 乐观锁双重保证订单不会在并发下被重复退款。
  大额退款（>阈值）还需 admin 二次审批，非 admin 批准只暂存 pending。

关键技术点:
  - 状态机校验(VALID_TRANSITIONS): 显式枚举合法跃迁集合，非法跃迁直接拒绝，防止越权跳转
  - 乐观锁(update_status_optimistic): 用 version 字段 CAS 更新，并发退款只有一个能成功
  - 幂等编排: 先查缓存命中直接返回，未命中才执行业务并回写，保证 at-most-once
  - 分级审批: 大额退款非 admin 批准 -> 保持 pending，避免普通坐席单方面放款

依赖关系:
  - 上游: app/api/v1/orders.py（退款申请/审批/撤销端点）
  - 下游: app.crud.order（持久化）、app.core.idempotency（幂等缓存）、app.config.settings（阈值）
"""

import logging
import math
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.idempotency import check_idempotency, record_idempotency
from app.crud import order as order_crud
from app.models.order import VALID_TRANSITIONS
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


class OrderError(Exception): pass
class InvalidTransition(OrderError): pass
class VersionConflict(OrderError): pass
class RefundExceedsPaid(OrderError): pass
class DuplicateRefund(OrderError): pass
class RefundError(Exception):
    """支付宝退款调用失败（非业务校验错误，属外部网关故障）。"""


def _check_transition(from_status: str, to_status: str) -> None:
    """校验状态跃迁合法性（私有 helper）。

    流程:
      1. 从 VALID_TRANSITIONS 取出当前状态的合法目标集合
      2. 目标状态不在集合内 -> 抛 InvalidTransition

    设计决策: 用白名单集合而非黑名单，新增状态时必须显式声明可达目标，
    避免"忘记禁止某条路径"导致越权跳转。
    """
    # 取合法目标集合；未定义的状态默认无任何合法跃迁（fail-closed）
    valid = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in valid:
        raise InvalidTransition(f"非法状态跃迁: {from_status} -> {to_status} (合法: {valid})")


def request_refund(
    db: Session, *, tenant_id: int, order_no: str, amount: float,
    reason: str | None, client_request_id: str,
) -> dict:
    """申请退款（资金逆流入口，三道防线兜底）。

    流程:
      1. 幂等检查: 命中 client_request_id 直接返回缓存结果，拦截网络重试
      2. 查询订单: 按 (tenant_id, order_no) 定位，租户隔离
      3. 金额校验: amount 必须 >0 且 <= paid_amount（实付金额）
      4. 状态机校验: 仅 paid 状态可发起退款 -> refunding
      5. 乐观锁更新: CAS 把订单状态从 paid 改为 refunding，并发只有一个成功
      6. 创建退款审批记录: 只追加不覆写，保留审计轨迹
      7. 记录幂等键: 绑定 client_request_id -> approval.id

    设计决策:
      - 幂等检查在最前：即使后续校验失败，重复请求也不会重复扣减
      - 乐观锁而非悲观锁：退款是低频操作，CAS 比加行锁开销更小
      - 审批记录只追加：退款可被 reject/cancel，但审批流水不可删改

    Args:
        db: 数据库会话
        tenant_id: 租户 ID（租户隔离）
        order_no: 订单号
        amount: 退款金额
        reason: 退款原因（可选）
        client_request_id: 客户端幂等键

    Returns:
        dict: approval_id / order_id / amount / status / client_request_id / cached
    """
    # 1. 查询订单 —— 按 (tenant_id, order_no) 定位，天然租户隔离
    #    （幂等检查需要 order.id 作 key 粒度，必须先定位订单；只读查询无副作用）
    order = order_crud.get_by_order_no(db, tenant_id, order_no)
    if order is None:
        raise OrderError(f"订单不存在: {order_no}")

    # 2. 幂等检查 —— key 粒度 = (order_id, client_request_id)：
    #    同一订单重试命中缓存；跨租户/跨订单同 id 互不干扰（防跨租户碰撞）
    cached = check_idempotency(order.id, client_request_id)
    if cached:
        logger.info(f"Refund idempotency hit: {client_request_id}")
        # L5: 缓存命中返回与首次一致的结构——原 {resource_type, resource_id} 与首次
        # {id, order_id, amount, status, ...} 不一致，客户端重试拿不到 id/amount/status。
        # 按 resource_id 查审批（带租户过滤）组装完整结构；查不到时降级返回原始缓存。
        approval = order_crud.get_refund_approval(db, cached.get("resource_id"), tenant_id)
        if approval is not None:
            return {
                "id": approval.id, "order_id": approval.order_id,
                "amount": approval.amount, "status": approval.status,
                "client_request_id": approval.client_request_id, "cached": True,
            }
        return {**cached, "cached": True}

    # 3. 金额校验 —— 上限是实付金额而非订单原价，防止退多于付
    # T-遗留1: paid_amount 为 Numeric(Decimal)，float 入参须经 str 转 Decimal 精确比较
    # （float 二进制表示略小于字面值，如 199.95 -> 199.9499...，直接比较边界会误判）
    # L11: 类型防御——str/bool/NaN/Inf 直接比较会 TypeError 或恒 False 穿透
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount):
        raise OrderError("退款金额必须是有限数值")
    if amount <= 0:
        raise OrderError("退款金额必须大于 0")
    if Decimal(str(amount)) > order.paid_amount:
        raise RefundExceedsPaid(
            f"退款金额({amount})超过订单实付金额({order.paid_amount})"
        )

    # 4. 状态机校验: 只有 paid 状态可以退款 —— 防止已发货/已退款订单二次退款
    _check_transition(order.status, "refunding")

    # 5. 乐观锁更新状态 —— CAS 保证并发下只有一个退款请求能成功推进状态
    ok = order_crud.update_status_optimistic(db, order, "refunding")
    if not ok:
        raise VersionConflict("订单状态已变更，请刷新重试")

    # 6. 创建退款审批（只追加不覆写）—— 保留完整审计流水
    approval = order_crud.create_refund_approval(
        db, order_id=order.id, amount=amount, reason=reason, client_request_id=client_request_id,
    )
    db.commit()

    # 7. 记录幂等 —— 业务成功后才回写，失败操作不记录（允许重试）
    record_idempotency(order.id, client_request_id, "refund", approval.id)

    return {
        "id": approval.id, "order_id": order.id, "amount": amount,
        "status": approval.status, "client_request_id": client_request_id, "cached": False,
    }


def approve_refund(db: Session, *, approval_id: int, decision: str,
                   approver_id: int, tenant_id: int, role: str = "admin") -> dict:
    """审批退款: approve/reject.

    010: 大额退款（>阈值）非 admin 批准时保持 pending，待 admin 二次审批。
    通过 order.tenant_id 校验审批归属当前租户（RefundApproval 无 tenant_id 字段，
    经关联订单间接校验，防止跨租户审批）。

    流程:
      1. 加载审批记录，校验是否仍为 pending（已处理的不可重复审批）
      2. 通过关联订单的 tenant_id 间接校验租户归属（RefundApproval 无 tenant_id 字段）
      3. 大额退款分级: decision=approved 且金额>阈值 且 role!=admin -> 保持 pending
      4. 写入审批结果；CAS 先推进订单状态（approved->refunded / rejected->paid），
         approved 再调外部退款，失败回滚整个事务（防"钱已退但状态未动"的双重退款）

    设计决策:
      - 租户校验走关联订单而非审批表本身: RefundApproval 不冗余 tenant_id，避免数据不一致
      - 大额分级审批用"保持 pending"而非"拒绝": 普通坐席的批准意见保留，等 admin 复核

    Args:
        db: 数据库会话
        approval_id: 退款审批 ID
        decision: "approved" / "rejected"
        approver_id: 审批人 ID
        tenant_id: 租户 ID
        role: 审批人角色，默认 admin

    Returns:
        dict: approval_id / status / message(大额待二审时)
    """
    approval = order_crud.get_refund_approval(db, approval_id, tenant_id)
    if approval is None:
        raise OrderError(f"退款审批不存在: {approval_id}")
    if approval.status != "pending":
        raise OrderError(f"审批已处理: {approval.status}")

    # 校验审批归属：通过关联订单的 tenant_id 确认当前租户有权操作
    # —— RefundApproval 无 tenant_id 字段，只能经 order 间接校验，防跨租户审批
    order = order_crud.get_by_id(db, approval.order_id, tenant_id)
    if order is None:
        raise OrderError(f"退款审批不存在或无权操作: {approval_id}")

    # 010: 大额退款分级审批（非 admin 批准大额 -> 保持 pending 待二次审批）
    # 延迟 import 避免循环依赖
    from app.config import settings
    if (decision == "approved"
            and approval.amount > settings.refund_approval_threshold
            and role != "admin"):
        logger.info(f"Large refund {approval_id} ({approval.amount}) approved by {role}, pending admin review")
        approval.approver_id = approver_id  # 保留审批人，待 admin 二次审批
        db.commit()
        return {"id": approval.id, "status": "pending", "message": "大额退款需管理员二次审批"}

    approval.status = decision
    approval.approver_id = approver_id

    # 状态机校验: approved -> refunded, rejected -> paid
    target = "refunded" if decision == "approved" else "paid"
    _check_transition(order.status, target)

    # 先 CAS 推进订单状态，再调外部退款（防双重退款）:
    # 原顺序（先外部退款后 CAS）在 CAS 失败/网络超时时，外部钱已退而订单仍 refunding，
    # 重试会再次退款。CAS 先行保证并发下只有一个请求推进状态，失败即中止、不调外部。
    ok = order_crud.update_status_optimistic(db, order, target)
    if not ok:
        raise VersionConflict("订单状态已变更，请刷新重试")

    if decision == "approved":
        # 调支付宝退款接口（mock 模式直接成功）。失败则回滚整个事务:
        # 订单回 refunding、审批回 pending，待人工处理；重试不会重复退款
        # （CAS 已保证只有本事务推进过状态）。注: 网络超时假失败（钱实际已退但返回 False）
        # 需生产端退款对账兜底，demo mock 模式退款恒成功不受影响。
        ok_refund = _invoke_alipay_refund(db, order, approval.amount)
        if not ok_refund:
            db.rollback()
            raise RefundError("支付宝退款调用失败，订单保持退款中状态")

    # PRD 2.1 审计日志: 退款审批操作落库（同事务，commit 时一并持久化）
    log_audit(
        db, tenant_id=tenant_id, user_id=approver_id, action="refund_approve",
        target_type="refund_approval", target_id=approval.id,
        detail=f"decision={decision}, amount={approval.amount}, order_no={order.order_no}",
    )
    db.commit()
    return {"id": approval.id, "status": decision}


def _invoke_alipay_refund(db: Session, order, amount: float) -> bool:
    """调支付宝退款接口（私有 helper）。

    退款流水优先级（T8 防退错账）:
      1. order.payment_trade_no —— 订单自身的支付流水（真实支付接入后回填）
      2. 降级: 租户最近一笔已支付账单的 trade_no —— demo 无真实支付时的兜底；
         真实支付宝下订单与账单无关联会退错账，故打 warning 标记降级路径
      3. 兜底: 无任何流水用 order_no（mock 模式 refund 恒 True，不影响 demo）

    mock 模式 alipay_client.refund 直接返回 True。
    """
    try:
        from app.core.alipay_client import get_alipay_client
        from app.models.billing_invoice import BillingInvoice
        from sqlalchemy import select

        if order.payment_trade_no:
            trade_no = order.payment_trade_no  # 正常路径: 订单自身流水
        else:
            # 降级路径: 租户最近已支付账单的 trade_no（订单与账单仅经 tenant_id 关联）
            logger.warning(
                f"Order {order.order_no} has no payment_trade_no, falling back to tenant's "
                "latest paid invoice (demo fallback; may refund wrong flow in production)"
            )
            invoice = db.execute(
                select(BillingInvoice).where(
                    BillingInvoice.tenant_id == order.tenant_id,
                    BillingInvoice.status == "paid",
                    BillingInvoice.payment_trade_no.is_not(None),
                    # 真实 MySQL 修复: nulls_last() 是 PG/SQLite 语法, MySQL 报 1064;
                    # MySQL 的 DESC 默认 NULL 排最后, 直接 desc() 即可
                ).order_by(BillingInvoice.paid_at.desc())
            ).scalars().first()
            trade_no = invoice.payment_trade_no if invoice else order.order_no

        client = get_alipay_client()
        return client.refund(trade_no, amount)
    except Exception as e:
        logger.error(f"Alipay refund call failed (order={order.order_no}): {e}")
        return False


def cancel_refund(db: Session, *, approval_id: int, tenant_id: int) -> dict:
    """撤销售后申请（010 US1）。

    仅 pending 状态可撤销 -> cancelled。通过关联订单校验租户归属。

    流程:
      1. 加载审批记录，校验仍为 pending
      2. 通过关联订单间接校验租户归属
      3. 审批状态置为 cancelled 并提交

    设计决策:
      - 撤销只改审批状态，不删记录：保留撤销审计轨迹
      - 仅 pending 可撤销：已进入审批流程的不允许撤回，避免状态混乱

    Args:
        db: 数据库会话
        approval_id: 退款审批 ID
        tenant_id: 租户 ID

    Returns:
        dict: approval_id / status
    """
    approval = order_crud.get_refund_approval(db, approval_id, tenant_id)
    if approval is None:
        raise OrderError(f"退款审批不存在: {approval_id}")
    if approval.status != "pending":
        raise OrderError(f"退款已处理（{approval.status}），不可撤销")

    # 通过关联订单间接校验租户归属 —— 同 approve_refund
    order = order_crud.get_by_id(db, approval.order_id, tenant_id)
    if order is None:
        raise OrderError(f"退款审批不存在或无权操作: {approval_id}")

    _check_transition(order.status, "paid")
    ok = order_crud.update_status_optimistic(db, order, "paid")
    if not ok:
        raise VersionConflict("订单状态已变更，请刷新重试")
    approval.status = "cancelled"
    # PRD 2.1 审计日志: 退款撤销操作落库（同事务）
    log_audit(
        db, tenant_id=tenant_id, user_id=None, action="refund_cancel",
        target_type="refund_approval", target_id=approval.id,
        detail=f"amount={approval.amount}, order_no={order.order_no}",
    )
    db.commit()
    logger.info(f"Refund {approval_id} cancelled by tenant {tenant_id}")
    return {"id": approval.id, "status": "cancelled"}



def ship_order(db: Session, *, order_id: int, tenant_id: int, tracking_no: str) -> dict:
    """发货：paid -> shipped，写入物流单号（PRD 3.6，013 US6，修复报告 6.1）。

    流程:
      1. 查订单（tenant_id 隔离）
      2. 状态机校验（仅 paid 可发货 -> shipped）
      3. 乐观锁 CAS 更新状态 + 写 tracking_no
      4. 审计日志

    Args:
        order_id: 订单主键
        tenant_id: 租户 ID（隔离）
        tracking_no: 物流单号

    Returns:
        dict: id / order_no / status / tracking_no
    """
    order = order_crud.get_by_id(db, order_id, tenant_id)
    if order is None:
        raise OrderError(f"订单不存在: {order_id}")
    _check_transition(order.status, "shipped")
    ok = order_crud.update_status_optimistic(db, order, "shipped")
    if not ok:
        raise VersionConflict("订单状态已变更，请刷新重试")
    order.tracking_no = tracking_no
    log_audit(
        db, tenant_id=tenant_id, user_id=None, action="ship_order",
        target_type="order", target_id=order.id,
        detail=f"tracking_no={tracking_no}, order_no={order.order_no}",
    )
    db.commit()
    logger.info(f"Order {order_id} shipped, tracking_no={tracking_no}")
    return {"id": order.id, "order_no": order.order_no, "status": "shipped", "tracking_no": tracking_no}



def create_order(
    db: Session, *, tenant_id: int, order_no: str, amount: float,
    paid_amount: float | None = None, phone: str | None = None,
) -> dict:
    """\u521b\u5efa\u8ba2\u5355\uff08closure-plan P1-3\uff0cPRD 3.6\uff09\u3002

    \u6d41\u7a0b:
      1. \u91d1\u989d\u6821\u9a8c\uff08>0\u3001\u6709\u9650\u6570\u3001\u975e bool\uff09
      2. \u8ba2\u5355\u53f7\u5168\u5c40\u552f\u4e00\u6821\u9a8c\uff08DB unique\uff09
      3. \u4e8b\u52a1\u521b\u5efa status=created + \u5ba1\u8ba1\u65e5\u5fd7

    Returns:
        dict: id / order_no / status / amount / paid_amount
    """
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount) or amount <= 0:
        raise OrderError("\u8ba2\u5355\u91d1\u989d\u5fc5\u987b\u662f\u5927\u4e8e 0 \u7684\u6709\u9650\u6570\u503c")
    pay = paid_amount if paid_amount is not None else 0.0
    if not isinstance(pay, (int, float)) or isinstance(pay, bool) or not math.isfinite(pay) or pay < 0:
        raise OrderError("\u5b9e\u4ed8\u91d1\u989d\u4e0d\u80fd\u4e3a\u8d1f")

    from app.models.order import Order
    from sqlalchemy import select
    exists = db.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()
    if exists is not None:
        raise OrderError(f"\u8ba2\u5355\u53f7\u5df2\u5b58\u5728: {order_no}")

    order = Order(
        tenant_id=tenant_id, order_no=order_no,
        amount=Decimal(str(amount)), paid_amount=Decimal(str(pay)),
        status="created", version=1, phone=phone,
    )
    db.add(order)
    db.flush()
    log_audit(
        db, tenant_id=tenant_id, user_id=None, action="order_create",
        target_type="order", target_id=order.id,
        detail=f"order_no={order_no}, amount={amount}",
    )
    db.commit()
    db.refresh(order)
    logger.info(f"Order {order_no} created by tenant {tenant_id}")
    return {
        "id": order.id, "order_no": order.order_no,
        "status": order.status, "amount": float(order.amount), "paid_amount": float(order.paid_amount),
    }
