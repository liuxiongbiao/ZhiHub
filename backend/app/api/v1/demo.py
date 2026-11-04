"""产品内演示中心 API：只组织演示租户自己的固定故事，不开放通用数据写入。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.config import settings
from app.models.tenant import Tenant

router = APIRouter()


class DemoRestoreRequest(BaseModel):
    """恢复操作必须显式确认，避免误触。"""

    confirm: Literal["RESTORE_DEMO"]


def _tenant_id(request: Request) -> int:
    value = getattr(request.state, "tenant_id", None)
    if value is None:
        raise HTTPException(status_code=401, detail="未认证")
    return int(value)


_DEMO_SCENARIOS = [
    {
        "id": "knowledge",
        "title": "可信知识回答",
        "prompt": "你们支持七天无理由退货吗？退货流程是什么？",
        "outcome": "AI 根据政策回答并展示引用",
    },
    {
        "id": "order",
        "title": "查询真实订单",
        "prompt": "帮我查订单 ORD-DEMO-0003，手机号后四位 2222。",
        "outcome": "身份核验后调用订单工具返回物流",
    },
    {
        "id": "handoff",
        "title": "风险转人工",
        "prompt": "商品运输中破损了，你们能额外赔偿我 500 元吗？",
        "outcome": "资金风险规则触发，完整上下文转交坐席",
    },
]


@router.get("/status", dependencies=[Depends(require_role("admin", "agent"))])
def demo_status(request: Request, db: Session = Depends(get_db)):
    """返回当前租户可用的演示剧本，不包含账号密码。"""
    tenant = db.query(Tenant).filter_by(id=_tenant_id(request)).first()
    is_demo = bool(
        tenant
        and tenant.is_demo
        and tenant.status == "active"
        and settings.app_env in {"development", "test", "demo"}
    )
    role = getattr(request.state, "role", None)
    widget_api_key = None
    if is_demo and role == "admin":
        from app.services.demo_story_service import demo_widget_api_key

        widget_api_key = demo_widget_api_key(tenant.id)
    return {
        "is_demo": is_demo,
        "can_restore": is_demo and getattr(request.state, "role", None) == "admin",
        "customer_phone_last4": "2222" if is_demo else None,
        "order_no": "ORD-DEMO-0003" if is_demo else None,
        "widget_api_key": widget_api_key,
        "scenarios": _DEMO_SCENARIOS if is_demo else [],
    }


@router.post("/restore", dependencies=[Depends(require_role("admin"))])
def restore_demo(
    body: DemoRestoreRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """安全恢复演示基线；保留真实交互，只替换带 demo_seed 标记的故事数据。"""
    if settings.app_env not in {"development", "test", "demo"}:
        raise HTTPException(status_code=404, detail="演示恢复不可用")
    tenant_id = _tenant_id(request)
    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if tenant is None or not tenant.is_demo or tenant.status != "active":
        raise HTTPException(status_code=403, detail="仅演示租户可恢复基线")

    from app.demo import demo_seed

    try:
        summary = demo_seed.seed(db=db)
    except demo_seed.DemoSeedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if summary.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=409, detail="当前演示租户与活动基线不一致")
    return {
        "restored": True,
        "summary": summary,
        "message": "演示知识、业务记录、对话轨迹、运营趋势和分析报告已恢复",
    }
