"""
backend/app/services/order_service.py

职责: 【资金安全核心】订单状态机跃迁校验 + 乐观锁 + 退款金额上限校验 + 大额退款分级审批

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
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, select, update as sa_update
from sqlalchemy.orm import Session

from app.core.idempotency import check_idempotency, record_idempotency
from app.crud import order as order_crud
from app.models.order import Order, VALID_TRANSITIONS
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


class OrderError(Exception): pass
class InvalidTransition(OrderError): pass
class VersionConflict(OrderError): pass
class RefundExceedsPaid(OrderError): pass
class DuplicateRefund(OrderError): pass
class RefundError(Exception):
    """支付宝退款调用失败（非业务校验错误，属外部网关故障）。"""


def _track_order_state(order, from_status: str, to_status: str, operator: str | int | None) -> None:
    from app.services.analytics_service import track_event
    track_event(
        "order_state_changed",
        tenant_id=order.tenant_id,
        properties={
            "order_id": order.id,
            "order_no": order.order_no,
            "from_status": from_status,
            "to_status": to_status,
            "operator": operator,
        },
    )


def _track_refund_decision(approval, order, decision: str, approver_id: int) -> None:
    from app.services.analytics_service import track_event
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    latency = max(0, (now - approval.created_at).total_seconds()) if approval.created_at else 0
    track_event(
        "refund_approved" if decision == "approved" else "refund_rejected",
        tenant_id=order.tenant_id,
        properties={
            "order_id": order.id,
            "approval_id": approval.id,
            "approver_id": approver_id,
            "amount": approval.amount,
            "latency_seconds": round(latency, 3),
        },
    )


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


def _validate_refund_amount(amount: float, paid_amount: Decimal) -> None:
    """退款金额必须是有限正数，且不能超过订单实付金额。"""
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount):
        raise OrderError("退款金额必须是有限数值")
    if amount <= 0:
        raise OrderError("退款金额必须大于 0")
    if Decimal(str(amount)) > paid_amount:
        raise RefundExceedsPaid(
            f"退款金额({amount})超过订单实付金额({paid_amount})"
        )


def _verify_refund_phone(phone: str | None, bound_phone: str | None) -> None:
    """提供手机号时，后四位必须与订单绑定号码一致。"""
    if phone is None:
        return
    from app.utils.validators import verify_phone_suffix

    if not verify_phone_suffix(phone, bound_phone):
        raise OrderError("身份校验未通过：手机号与订单绑定信息不匹配")


def _cached_refund(db: Session, cached: dict, tenant_id: int, client_request_id: str) -> dict:
    """重复请求返回与首次相同的审批结构；查不到时保留原始缓存。"""
    logger.info(f"Refund idempotency hit: {client_request_id}")
    approval = order_crud.get_refund_approval(db, cached.get("resource_id"), tenant_id)
    if approval is not None:
        return {
            "id": approval.id,
            "order_id": approval.order_id,
            "amount": approval.amount,
            "status": approval.status,
            "client_request_id": approval.client_request_id,
            "cached": True,
        }
    return {**cached, "cached": True}


def _commit_refund_request(
    db: Session, order, amount: float, reason: str | None, client_request_id: str,
) -> dict:
    """用乐观锁进入待退款，并在成功后才写入幂等结果。"""
    pre_status = order.status
    if not order_crud.update_status_optimistic(db, order, "refund_requested"):
        raise VersionConflict("订单状态已变更，请刷新重试")

    approval = order_crud.create_refund_approval(
        db,
        order_id=order.id,
        amount=amount,
        reason=reason,
        client_request_id=client_request_id,
        pre_status=pre_status,
    )
    order_crud.create_refund_event(
        db,
        approval_id=approval.id,
        event_type="requested",
        actor_role="user",
        detail=f"pre_status={pre_status}, amount={amount}",
    )
    db.commit()
    _track_order_state(order, pre_status, "refund_requested", "end_user")
    record_idempotency(order.id, client_request_id, "refund", approval.id)
    return {
        "id": approval.id, "order_id": order.id, "amount": amount,
        "status": approval.status, "client_request_id": client_request_id, "cached": False,
    }


def request_refund(
    db: Session, *, tenant_id: int, order_no: str, amount: float,
    reason: str | None, client_request_id: str, phone: str | None = None,
) -> dict:
    """申请退款（资金逆流入口，三道防线兜底）。

    流程:
      1. 幂等检查: 命中 client_request_id 直接返回缓存结果，拦截网络重试
      2. 查询订单: 按 (tenant_id, order_no) 定位，租户隔离
      3. 金额校验: amount 必须 >0 且 <= paid_amount（实付金额）
      4. 身份校验（规格 016）: 提供 phone 时后 4 位必须与订单绑定手机号一致
      5. 状态机校验: paid/shipped 可发起退款 -> refund_requested
      6. 乐观锁更新: CAS 把订单状态改为 refund_requested，并发只有一个成功
      7. 创建退款审批记录: 只追加不覆写，保留审计轨迹
      8. 记录幂等键: 绑定 client_request_id -> approval.id

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
    # （幂等检查需要 order.id 作 key 粒度，必须先定位订单；只读查询无副作用）
    order = order_crud.get_by_order_no(db, tenant_id, order_no)
    if order is None:
        raise OrderError(f"订单不存在: {order_no}")

    _verify_refund_phone(phone, order.phone)

    cached = check_idempotency(order.id, client_request_id)
    if cached:
        return _cached_refund(db, cached, tenant_id, client_request_id)

    _validate_refund_amount(amount, order.paid_amount)

    # 5. 状态机校验: paid/shipped 可退款，终态/已退款订单拒绝
    _check_transition(order.status, "refund_requested")
    # 6-8. 乐观锁推进、审批流水和幂等回写。失败不记录幂等，允许重试。
    return _commit_refund_request(db, order, amount, reason, client_request_id)


def get_refund_approval_threshold(db: Session, tenant_id: int) -> float:
    """返回租户客服单笔退款终审额度，租户未配置时使用全局默认值。"""
    from app.config import settings
    from app.models.tenant import Tenant

    tenant = db.get(Tenant, tenant_id)
    return (
        float(tenant.refund_approval_threshold)
        if tenant and tenant.refund_approval_threshold
        else settings.refund_approval_threshold
    )


def _record_agent_pre_review(
    db: Session, approval, *, decision: str, approver_id: int, role: str, threshold: float,
) -> dict:
    """客服超额时只记录初审，审批保持待管理员终审。"""
    event_type = "pre_approved" if decision == "approved" else "pre_rejected"
    detail = f"amount={approval.amount}, threshold={threshold}, pending admin review"
    order_crud.create_refund_event(
        db,
        approval_id=approval.id,
        event_type=event_type,
        actor_id=approver_id,
        actor_role=role,
        detail=detail,
    )
    _append_review(
        approval,
        "pre_approve" if decision == "approved" else "pre_reject",
        approver_id,
        detail,
    )
    db.commit()
    return {
        "id": approval.id,
        "status": "pending",
        "message": f"退款金额超过客服审批额度 ¥{threshold:.2f}，已提交管理员二次审批复核",
        "requires_admin_review": True,
    }


def _track_refund_progress(approval, order, decision: str, approver_id: int, to_status: str) -> None:
    """记录审批决定，以及订单从待退款进入的新状态。"""
    _track_refund_decision(approval, order, decision, approver_id)
    _track_order_state(order, "refund_requested", to_status, approver_id)


def _pending_refund_target(db: Session, approval_id: int, tenant_id: int, decision: str, role: str):
    """校验决定、角色和待审批记录。已处理时返回结果，否则返回审批、订单和额度。"""
    if decision not in {"approved", "rejected"}:
        raise OrderError("decision 必须为 approved 或 rejected")
    if role not in {"admin", "agent"}:
        raise OrderError("当前角色无退款审批权限")

    approval = order_crud.get_refund_approval(db, approval_id, tenant_id)
    if approval is None:
        raise OrderError(f"退款审批不存在: {approval_id}")
    if approval.status != "pending":
        return {
            "id": approval.id,
            "status": approval.status,
            "already_processed": True,
        }, None, None

    # 校验审批归属：通过关联订单的 tenant_id 确认当前租户有权操作
    # —— RefundApproval 无 tenant_id 字段，只能经 order 间接校验，防跨租户审批
    order = order_crud.get_by_id(db, approval.order_id, tenant_id)
    if order is None:
        raise OrderError(f"退款审批不存在或无权操作: {approval_id}")
    return approval, order, get_refund_approval_threshold(db, order.tenant_id)


def _claim_final_refund(db: Session, approval, approval_id: int, tenant_id: int, decision: str, approver_id: int):
    """以 pending 为条件原子领取。没抢到时回滚并返回已处理结果。"""
    if order_crud.claim_refund_decision(
        db,
        approval_id=approval.id,
        decision=decision,
        approver_id=approver_id,
    ):
        return None
    db.rollback()
    current = order_crud.get_refund_approval(db, approval_id, tenant_id)
    return {
        "id": approval_id,
        "status": current.status if current is not None else "processed",
        "already_processed": True,
    }


def _record_final_refund(db: Session, approval, order, decision: str, approver_id: int, tenant_id: int, role: str, threshold) -> None:
    """终审抢占成功后追加审批链、事件和审计。"""
    db.expire(approval)
    db.refresh(approval)
    _append_review(approval, decision, approver_id, f"decision={decision}")
    order_crud.create_refund_event(
        db,
        approval_id=approval.id,
        event_type=decision,
        actor_id=approver_id,
        actor_role=role,
        detail=f"amount={approval.amount}, threshold={threshold}",
    )
    log_audit(
        db, tenant_id=tenant_id, user_id=approver_id, action="refund_approve",
        target_type="refund_approval", target_id=approval.id,
        detail=f"decision={decision}, amount={approval.amount}, order_no={order.order_no}",
    )


def _restore_rejected_refund(db: Session, approval, order, decision: str, approver_id: int) -> dict:
    """拒绝后退回申请前状态。乐观锁失败时不提交。"""
    restore_status = approval.pre_status or "paid"
    _check_transition(order.status, restore_status)
    if not order_crud.update_status_optimistic(db, order, restore_status):
        raise VersionConflict("订单状态已变更，请刷新重试")
    db.commit()
    _track_refund_progress(approval, order, decision, approver_id, restore_status)
    return {"id": approval.id, "status": decision}


def _enqueue_approved_refund(db: Session, approval, order, decision: str, approver_id: int) -> dict:
    """通过后进入退款中，写出站记录，并同步尝试一次渠道退款。"""
    if order.status == "refund_requested":
        _check_transition(order.status, "refunding")
        if not order_crud.update_status_optimistic(db, order, "refunding"):
            raise VersionConflict("订单状态已变更，请刷新重试")
    elif order.status != "refunding":
        raise InvalidTransition(f"退款审批对应订单状态异常: {order.status}")

    from app.models.refund_outbox import RefundOutbox
    try:
        trade_no = _resolve_refund_trade_no(db, order)
    except RefundError:
        # 无支付流水 → failed outbox 标记人工处理，禁止猜测退款目标
        db.add(RefundOutbox(
            approval_id=approval.id, order_id=order.id,
            amount=approval.amount, trade_no=None, status="failed",
            last_error="missing payment_trade_no, manual review required",
        ))
        db.commit()
        _track_refund_progress(approval, order, decision, approver_id, "refunding")
        raise

    db.add(RefundOutbox(
        approval_id=approval.id, order_id=order.id,
        amount=approval.amount, trade_no=trade_no, status="pending",
    ))
    db.commit()
    _track_refund_progress(approval, order, decision, approver_id, "refunding")

    # 同步尽力执行（mock/单实例部署）；失败进入 pending 由后台任务重试
    if process_refund_outbox(db, approval.id) is False:
        raise RefundError("支付宝退款调用失败，已进入重试队列（订单保持退款中状态）")
    return {"id": approval.id, "status": decision}


def approve_refund(db: Session, *, approval_id: int, decision: str,
                   approver_id: int, tenant_id: int, role: str = "admin") -> dict:
    """审批退款：客服额度内可终审，超额只记录初审；终审用条件更新抢占。"""
    approval, order, threshold = _pending_refund_target(db, approval_id, tenant_id, decision, role)
    if order is None:
        return approval

    if role == "agent" and approval.amount > Decimal(str(threshold)):
        return _record_agent_pre_review(
            db, approval, decision=decision, approver_id=approver_id, role=role, threshold=threshold,
        )

    claimed = _claim_final_refund(db, approval, approval_id, tenant_id, decision, approver_id)
    if claimed is not None:
        return claimed

    _record_final_refund(db, approval, order, decision, approver_id, tenant_id, role, threshold)
    if decision == "rejected":
        return _restore_rejected_refund(db, approval, order, decision, approver_id)
    return _enqueue_approved_refund(db, approval, order, decision, approver_id)


def _append_review(approval, action: str, approver_id: int | None, detail: str | None = None) -> None:
    """: 审批链只追加（JSON 数组），历史记录不可覆写。"""
    import json
    from datetime import datetime, timezone

    history: list = []
    if approval.review_history:
        try:
            history = json.loads(approval.review_history)
        except (json.JSONDecodeError, TypeError):
            history = []
    history.append({
        "action": action,
        "approver_id": approver_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    })
    approval.review_history = json.dumps(history, ensure_ascii=False)


def _resolve_refund_trade_no(db: Session, order) -> str:
    """退款流水解析（ 防退错账）：订单流水 > 租户最近已付账单流水 > order_no 兜底。"""
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select

    if order.payment_trade_no:
        return order.payment_trade_no
    # 资金路径 fail-closed —— 无精确支付流水时拒绝执行退款并转人工，
    # 禁止用“最近账单流水/订单号”猜测退款目标（可能退错账）。
    raise RefundError(
        f"订单 {order.order_no} 缺少支付流水（payment_trade_no），退款已拒绝执行，请人工处理"
    )


def refund_request_no(approval_id: int) -> str:
    """由审批主键确定性派生渠道幂等号，所有重试复用同一值。"""
    return f"ZH-REFUND-{approval_id}"


def _mark_refund_success(db: Session, outbox, order) -> bool:
    """把渠道成功结果投影到 outbox 和订单；不会再次触达渠道。"""
    from app.models.refund_outbox import RefundOutbox

    db.execute(
        sa_update(RefundOutbox)
        .where(RefundOutbox.id == outbox.id, RefundOutbox.status == "processing")
        .values(status="success", processing_at=None, last_error=None)
    )
    if order.status == "refunded":
        db.commit()
        return True
    from_status = order.status
    cas_ok = order_crud.update_status_optimistic(db, order, "refunded")
    if not cas_ok:
        db.execute(
            sa_update(RefundOutbox)
            .where(RefundOutbox.id == outbox.id)
            .values(last_error="order CAS conflict, manual review required")
        )
    db.commit()
    if cas_ok:
        _track_order_state(order, from_status, "refunded", "refund_worker")
    return cas_ok


def _claimable_refund_clause(now: datetime, lease_seconds: int):
    """待执行，或处理租约已经过期的退款出站记录。"""
    from app.models.refund_outbox import RefundOutbox

    stale_before = now - timedelta(seconds=lease_seconds)
    return or_(
        RefundOutbox.status == "pending",
        (
            (RefundOutbox.status == "processing")
            & (
                RefundOutbox.processing_at.is_(None)
                | (RefundOutbox.processing_at <= stale_before)
            )
        ),
    )


def _update_processing_outbox(db: Session, outbox_id: int, **values) -> None:
    """只更新仍处于 processing 的记录，避免覆盖并发领取后的新状态。"""
    from app.models.refund_outbox import RefundOutbox

    db.execute(
        sa_update(RefundOutbox)
        .where(RefundOutbox.id == outbox_id, RefundOutbox.status == "processing")
        .values(**values)
    )


def _notify_refund_manual_review(db: Session, order, max_attempts: int) -> None:
    """自动重试耗尽后通知人工处理。通知失败不改变退款状态。"""
    try:
        from app.services.notification_service import create_notification

        create_notification(
            db,
            tenant_id=order.tenant_id,
            title="退款执行失败，需人工处理",
            content=f"订单 {order.order_no} 的退款已重试 {max_attempts} 次仍失败，已停止自动重试，请人工处理。",
            type="refund_failed",
        )
    except Exception as exc:
        logger.warning(f"Refund failed notification error (order={order.order_no}): {exc}")


def _claim_refund_outbox(db: Session, approval_id: int, lease_seconds: int):
    """领取一条待执行或租约过期的出站记录。没有可领取记录时返回 None。"""
    from app.models.refund_outbox import RefundOutbox

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    claimable = _claimable_refund_clause(now, lease_seconds)
    outbox = db.execute(
        select(RefundOutbox).where(
            RefundOutbox.approval_id == approval_id,
            claimable,
        )
    ).scalar_one_or_none()
    if outbox is None:
        return None

    previous_attempts = outbox.attempts
    claimed = db.execute(
        sa_update(RefundOutbox)
        .where(RefundOutbox.id == outbox.id, claimable)
        .values(
            status="processing",
            processing_at=now,
            attempts=RefundOutbox.attempts + 1,
        )
    )
    if claimed.rowcount == 0:
        return None
    db.commit()
    return outbox, previous_attempts


def _fail_missing_refund_order(db: Session, outbox) -> bool:
    """订单已经不存在时，把出站记录标为失败。"""
    from app.models.refund_outbox import RefundOutbox

    db.execute(
        sa_update(RefundOutbox)
        .where(RefundOutbox.id == outbox.id)
        .values(status="failed", processing_at=None, last_error="order missing")
    )
    db.commit()
    return False


def _reconcile_previous_refund(db: Session, outbox, order, previous_attempts: int, request_no: str):
    """重试前先向渠道对账。返回 None 表示还需要重新发起退款。"""
    if previous_attempts <= 0:
        return None
    channel_status = _query_alipay_refund(outbox.trade_no, request_no)
    if channel_status is True:
        return _mark_refund_success(db, outbox, order)
    if channel_status is None:
        _update_processing_outbox(
            db,
            outbox.id,
            status="pending",
            processing_at=None,
            last_error="refund status unknown; reconciliation required before retry",
        )
        db.commit()
        return False
    return None


def _park_failed_refund_attempt(db: Session, outbox, order, previous_attempts: int, max_attempts: int) -> bool:
    """渠道拒绝后放回待重试；次数用尽则失败并通知人工。"""
    attempt_count = previous_attempts + 1
    if attempt_count >= max_attempts:
        _update_processing_outbox(
            db,
            outbox.id,
            status="failed",
            processing_at=None,
            last_error="max attempts reached, manual review required",
        )
        _notify_refund_manual_review(db, order, max_attempts)
    else:
        _update_processing_outbox(
            db,
            outbox.id,
            status="pending",
            processing_at=None,
            last_error="alipay refund rejected",
        )
    db.commit()
    return False


def process_refund_outbox(
    db: Session,
    approval_id: int,
    max_attempts: int = 10,
    lease_seconds: int = 300,
) -> bool | None:
    """执行 pending 或租约过期的 processing 退款，重试前先向渠道对账。"""
    claimed = _claim_refund_outbox(db, approval_id, lease_seconds)
    if claimed is None:
        return None
    outbox, previous_attempts = claimed

    order = db.get(Order, outbox.order_id)
    if order is None:
        return _fail_missing_refund_order(db, outbox)

    request_no = refund_request_no(outbox.approval_id)
    reconciled = _reconcile_previous_refund(db, outbox, order, previous_attempts, request_no)
    if reconciled is not None:
        return reconciled

    if _invoke_alipay_refund(
        db,
        order,
        float(outbox.amount),
        outbox.trade_no,
        request_no,
    ):
        return _mark_refund_success(db, outbox, order)
    return _park_failed_refund_attempt(db, outbox, order, previous_attempts, max_attempts)


def process_refund_outbox_batch(
    db: Session,
    max_attempts: int = 10,
    lease_seconds: int = 300,
) -> dict:
    """批量处理 pending，并恢复租约超时的 processing 记录。"""
    from app.models.refund_outbox import RefundOutbox

    stale_before = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=lease_seconds)
    rows = db.execute(
        select(RefundOutbox).where(
            or_(
                (
                    (RefundOutbox.status == "pending")
                    & (RefundOutbox.attempts < max_attempts)
                ),
                (
                    (RefundOutbox.status == "processing")
                    & (
                        RefundOutbox.processing_at.is_(None)
                        | (RefundOutbox.processing_at <= stale_before)
                    )
                ),
            )
        )
    ).scalars().all()
    result = {"success": 0, "failed": 0}
    for approval_id in [row.approval_id for row in rows]:
        ok = process_refund_outbox(
            db,
            approval_id,
            max_attempts=max_attempts,
            lease_seconds=lease_seconds,
        )
        if ok is True:
            result["success"] += 1
        elif ok is False:
            result["failed"] += 1
    return result


def _query_alipay_refund(trade_no: str | None, request_no: str) -> bool | None:
    """查询渠道退款结果；缺少支付流水时状态不可确认。"""
    if not trade_no:
        return None
    try:
        from app.core.alipay_client import get_alipay_client

        return get_alipay_client().query_refund(trade_no, request_no)
    except Exception as e:
        logger.error("Alipay refund query failed (request_no=%s): %s", request_no, e)
        return None


def _invoke_alipay_refund(
    db: Session,
    order,
    amount: float,
    trade_no: str | None = None,
    request_no: str | None = None,
) -> bool:
    """调支付宝退款接口（私有 helper）。

    退款流水优先级（ 防退错账）:
      1. order.payment_trade_no —— 订单自身的支付流水（真实支付接入后回填）
      2. 降级: 租户最近一笔已支付账单的 trade_no —— demo 无真实支付时的兜底；
         真实支付宝下订单与账单无关联会退错账，故打 warning 标记降级路径
      3. 兜底: 无任何流水用 order_no（mock 模式 refund 恒 True，不影响 demo）

    mock 模式 alipay_client.refund 直接返回 True。
    """
    try:
        from app.core.alipay_client import get_alipay_client

        if trade_no is None:
            trade_no = _resolve_refund_trade_no(db, order)

        client = get_alipay_client()
        return client.refund(trade_no, amount, request_no=request_no)
    except Exception as e:
        logger.error(f"Alipay refund call failed (order={order.order_no}): {e}")
        return False


def _pending_refund_for_cancel(db: Session, approval_id: int, tenant_id: int, reason: str | None, phone: str | None):
    """读取仍可撤销的审批和订单。原因、状态、租户和手机号都在这里拒绝。"""
    approval = order_crud.get_refund_approval(db, approval_id, tenant_id)
    if approval is None:
        raise OrderError(f"退款审批不存在: {approval_id}")
    if not reason or not reason.strip():
        raise OrderError("请填写撤销原因")
    if approval.status != "pending":
        raise OrderError(f"退款已处理（{approval.status}），不可撤销")

    order = order_crud.get_by_id(db, approval.order_id, tenant_id)
    if order is None:
        raise OrderError(f"退款审批不存在或无权操作: {approval_id}")
    _verify_refund_phone(phone, order.phone)
    return approval, order


def _restore_cancelled_refund(db: Session, approval, order, approval_id: int, tenant_id: int, reason: str) -> dict:
    """抢占撤销并按申请前状态退回订单。没抢到时返回已处理结果。"""
    restore_status = approval.pre_status or "paid"
    if not order_crud.claim_refund_decision(
        db,
        approval_id=approval.id,
        decision="cancelled",
        approver_id=None,
    ):
        db.rollback()
        current = order_crud.get_refund_approval(db, approval_id, tenant_id)
        return {
            "id": approval_id,
            "status": current.status if current is not None else "processed",
            "already_processed": True,
        }

    db.expire(approval)
    db.refresh(approval)
    _check_transition(order.status, restore_status)
    if not order_crud.update_status_optimistic(db, order, restore_status):
        raise VersionConflict("订单状态已变更，请刷新重试")
    cleaned_reason = reason.strip()
    _append_review(approval, "cancel", None, f"用户/管理员撤销售后申请：{cleaned_reason}")
    order_crud.create_refund_event(
        db,
        approval_id=approval.id,
        event_type="cancelled",
        actor_role="user",
        detail=cleaned_reason,
    )
    log_audit(
        db, tenant_id=tenant_id, user_id=None, action="refund_cancel",
        target_type="refund_approval", target_id=approval.id,
        detail=f"amount={approval.amount}, order_no={order.order_no}, reason={cleaned_reason}",
    )
    db.commit()
    _track_order_state(order, "refund_requested", restore_status, "tenant")
    logger.info(f"Refund {approval_id} cancelled by tenant {tenant_id}")
    return {"id": approval.id, "status": "cancelled"}


def cancel_refund(
    db: Session, *, approval_id: int, tenant_id: int, phone: str | None = None,
    reason: str | None = None,
) -> dict:
    """撤销售后申请（规格 010 US1）。

    仅 pending 状态可撤销 -> cancelled。通过关联订单校验租户归属。
    PRD 3.6：撤销必须填写原因并记录。

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
    approval, order = _pending_refund_for_cancel(db, approval_id, tenant_id, reason, phone)
    return _restore_cancelled_refund(db, approval, order, approval_id, tenant_id, reason)



def ship_order(db: Session, *, order_id: int, tenant_id: int, tracking_no: str) -> dict:
    """发货：paid -> shipped，写入物流单号（PRD 3.6，规格 013 US6，修复报告 6.1）。

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
    _track_order_state(order, "paid", "shipped", "staff")
    logger.info(f"Order {order_id} shipped, tracking_no={tracking_no}")
    return {"id": order.id, "order_no": order.order_no, "status": "shipped", "tracking_no": tracking_no}



def create_order(
    db: Session, *, tenant_id: int, order_no: str, amount: float,
    paid_amount: float | None = None, phone: str | None = None, created_by: str = "staff",
) -> dict:
    """\u521b\u5efa\u8ba2\u5355\uff08\uff0cPRD 3.6\uff09\u3002

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
        status="created", version=1, phone=phone, created_by=created_by,
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
