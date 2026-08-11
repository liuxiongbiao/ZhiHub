"""
backend/app/api/v1/order.py

职责: 订单列表/详情、退款列表、退款审批/撤销
对应日程: Day 5（订单）+ 010 US1（退款闭环）

端点概览:
  GET  /list                         订单列表（分页）        -> order_crud.list_orders
  GET  /{order_id}                   订单详情                -> order_crud.get_by_id
  GET  /refunds/list                 退款审批列表（分页）    -> order_crud.list_refund_approvals
  POST /refunds/{id}/approve         退款审批（approved/rejected）-> order_service.approve_refund
  POST /refunds/{id}/cancel          撤销售后申请（仅 pending）  -> order_service.cancel_refund

权限: 需登录；退款审批需 admin 角色（在 service 层校验 role）

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/order
  - 下游: app.crud.order、app.services.order_service
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.crud import order as order_crud

router = APIRouter()


def _get_tenant_id(request: Request) -> int:
    """从 request.state 提取 tenant_id，未认证 -> 401。"""
    tid = getattr(request.state, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=401, detail="未认证")
    return tid


@router.get("/list")
def list_orders(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """订单列表（分页）: 调 order_crud.list_orders。"""
    tenant_id = _get_tenant_id(request)
    items, total = order_crud.list_orders(db, tenant_id, page=page, page_size=page_size)
    return {"items": [{"id": o.id, "order_no": o.order_no, "amount": o.amount, "paid_amount": o.paid_amount, "status": o.status, "tracking_no": o.tracking_no, "created_at": o.created_at} for o in items], "total": total, "page": page, "page_size": page_size}


@router.get("/{order_id}")
def get_order_detail(order_id: int, request: Request, db: Session = Depends(get_db)):
    """订单详情: 调 order_crud.get_by_id，不存在 -> 404。"""
    tenant_id = _get_tenant_id(request)
    order = order_crud.get_by_id(db, order_id, tenant_id)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"id": order.id, "order_no": order.order_no, "amount": order.amount, "paid_amount": order.paid_amount, "status": order.status, "tracking_no": order.tracking_no}


@router.get("/refunds/list")
def list_refunds(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), _: None = Depends(require_role("admin"))):
    """退款审批列表（分页，按租户隔离）: 调 order_crud.list_refund_approvals。"""
    tenant_id = _get_tenant_id(request)
    items, total = order_crud.list_refund_approvals(db, tenant_id, page=page, page_size=page_size)
    return {"items": [{"id": r.id, "order_id": r.order_id, "amount": r.amount, "reason": r.reason, "status": r.status, "created_at": r.created_at} for r in items], "total": total, "page": page, "page_size": page_size}


class ApproveRefundRequest(BaseModel):
    decision: str = "approved"  # approved | rejected


@router.post("/refunds/{approval_id}/approve")
def approve_refund(approval_id: int, request: Request, body: ApproveRefundRequest, db: Session = Depends(get_db)):
    """退款审批: 调 order_service.approve_refund，role 从 request.state 取（service 层校验权限）。"""
    from app.services.order_service import approve_refund, OrderError
    tenant_id = _get_tenant_id(request)
    approver_id = getattr(request.state, "user_id", 0)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision 必须为 approved 或 rejected")
    try:
        role = getattr(request.state, "role", "user")
        result = approve_refund(
            db, approval_id=approval_id, decision=body.decision,
            approver_id=approver_id, tenant_id=tenant_id, role=role,
        )
        return result
    except OrderError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/refunds/{approval_id}/cancel")
def cancel_refund(approval_id: int, request: Request, db: Session = Depends(get_db), _: None = Depends(require_role("admin"))):
    """撤销售后申请（010 US1）: 仅 pending 状态可撤销，调 order_service.cancel_refund。"""
    from app.services.order_service import cancel_refund, OrderError
    tenant_id = _get_tenant_id(request)
    try:
        return cancel_refund(db, approval_id=approval_id, tenant_id=tenant_id)
    except OrderError as e:
        status_code = 409 if "不可撤销" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))



class ShipRequest(BaseModel):
    tracking_no: str  # 1-100 字符，由 schema min_length 强制（见端点）


@router.post("/{order_id}/ship")
def ship_order(order_id: int, request: Request, body: ShipRequest, db: Session = Depends(get_db)):
    """订单发货：paid -> shipped，记录物流单号（PRD 3.6，013 US6）。

    仅 paid 状态可发货；shipped/completed 等状态 -> 400（非法跃迁）。
    跨租户 -> 404（order_crud.get_by_id 按 tenant_id 隔离）。
    """
    if not body.tracking_no or len(body.tracking_no) > 100:
        raise HTTPException(status_code=422, detail="tracking_no 必填且不超过 100 字符")
    from app.services.order_service import ship_order as do_ship, OrderError, VersionConflict
    tenant_id = _get_tenant_id(request)
    try:
        return do_ship(db, order_id=order_id, tenant_id=tenant_id, tracking_no=body.tracking_no)
    except VersionConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except OrderError as e:
        # 非法状态跃迁 / 订单不存在 统一映射；不存在 -> 404，非法跃迁 -> 400
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(status_code=404, detail=msg) from e
        raise HTTPException(status_code=400, detail=msg) from e
