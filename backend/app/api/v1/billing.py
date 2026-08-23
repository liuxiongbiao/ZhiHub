"""
backend/app/api/v1/billing.py

职责: 配额查询、月度账单列表、用量概览

端点概览:
  GET /quota     Redis 实时配额使用情况      -> billing_service.check_quota
  GET /invoices  月度账单列表（DB 分页）      -> 直接查 BillingInvoice
  GET /usage     用量概览（配额 + 聚合数据）  -> billing_service.check_quota

权限: 需登录 + 企业管理员角色（PRD 3.8：账单与用量仅管理员可见，坐席不可访问）

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/billing
  - 下游: app.services.billing_service、app.models.billing_invoice、app.api.deps.require_role
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.api.deps import get_db, require_role

router = APIRouter()


def _get_tenant_id(request: Request) -> int:
    """从 request.state 提取 tenant_id，未认证 -> 401。"""
    tid = getattr(request.state, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=401, detail="未认证")
    return tid


@router.get("/quota", dependencies=[Depends(require_role("admin"))])
def get_quota(request: Request, db: Session = Depends(get_db)):
    """查询 Redis 实时配额（按套餐矩阵），调 billing_service.check_quota。仅管理员。"""
    from app.services.billing_service import check_quota, get_tenant_quota
    return check_quota(_get_tenant_id(request), quota=get_tenant_quota(db, _get_tenant_id(request)))


@router.get("/invoices", dependencies=[Depends(require_role("admin"))])
def list_invoices(request: Request, db: Session = Depends(get_db)):
    """月度账单列表，按创建时间倒序，直接查 BillingInvoice 表。仅管理员。"""
    from app.models.billing_invoice import BillingInvoice
    from sqlalchemy import select
    tenant_id = _get_tenant_id(request)
    invoices = db.execute(
        select(BillingInvoice).where(BillingInvoice.tenant_id == tenant_id).order_by(BillingInvoice.created_at.desc())
    ).scalars().all()
    return {"items": [{"id": i.id, "period": i.period, "base_fee": i.base_fee, "overage_fee": i.overage_fee, "status": i.status, "paid_at": i.paid_at} for i in invoices]}


@router.get("/usage", dependencies=[Depends(require_role("admin"))])
def usage_overview(request: Request, db: Session = Depends(get_db)):
    """用量概览（按套餐配额），复用 billing_service.check_quota。仅管理员。"""
    from app.services.billing_service import check_quota, get_tenant_quota
    tid = _get_tenant_id(request)
    return {"quota": check_quota(tid, quota=get_tenant_quota(db, tid))}


@router.get("/usage/export", dependencies=[Depends(require_role("admin"))])
def export_usage(request: Request, db: Session = Depends(get_db)):
    """用量明细 CSV 导出（PRD 3.9，审查 ）。仅 admin。"""
    from app.services.billing_service import export_usage as _export
    csv_text = _export(db, tenant_id=_get_tenant_id(request))
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="zhihub-usage.csv"'},
    )


@router.get("/usage/export-detail", dependencies=[Depends(require_role("admin"))])
def export_usage_detail(request: Request, db: Session = Depends(get_db)):
    """用量与 Token 消耗明细 CSV 导出（PRD 3.9）：会话ID/时间/意图/模型/Token/是否超额。仅 admin。"""
    from app.services.billing_service import export_usage_detail as _export_detail

    csv_text = _export_detail(db, tenant_id=_get_tenant_id(request))
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="zhihub-usage-detail.csv"'},
    )


@router.post("/pay/{invoice_id}", dependencies=[Depends(require_role("admin"))])
def pay_invoice(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    """生成支付宝订单 + 支付链接（PRD 3.10）。仅管理员。

    流程: 校验账单归属与状态 -> 生成 trade_no -> 调 alipay_client.create_payment 返回跳转 URL。
    """
    from app.services.billing_service import create_payment, PaymentError
    try:
        return create_payment(db, invoice_id=invoice_id, tenant_id=_get_tenant_id(request))
    except PaymentError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/alipay/notify")
async def alipay_notify(request: Request, db: Session = Depends(get_db)):
    """支付宝异步回调端点（公开免鉴权，PRD 3.10）。

    流程: 接收表单 -> 验签 -> 幂等去重 -> 更新账单 paid + trade_no + paid_at -> 恢复降级。
    支付宝约定: 成功返回 "success" 字符串，失败返回非 success。

    公开路径（auth_middleware 白名单 /api/v1/billing/alipay/notify），安全性由支付宝签名校验保证。
    """
    from app.services.billing_service import handle_payment_callback, PaymentError

    form = await request.form()
    form_data = {k: v for k, v in form.items()}
    try:
        handle_payment_callback(db, form_data=form_data)
        return "success"
    except PaymentError as e:
        # 验签失败 → 400，支付宝会重试；但签名不合法的重试也不会通过
        raise HTTPException(status_code=400, detail=str(e))
