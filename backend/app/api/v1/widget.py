"""规格 014 Widget 公共通道 API：匿名会话创建 / 发消息 / 撤销售后申请。

业务规则来源: PRD §3.1.3 / docs/specs/014-widget-public-channel/spec.md
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.session import SendMessageRequest, SendMessageResponse

router = APIRouter()


def _get_tenant_id(request: Request) -> int:
    tid = getattr(request.state, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=401, detail="未认证")
    return tid


class WidgetSessionCreateRequest(BaseModel):
    """创建匿名会话：携带租户 API Key；origin 可选（缺省用 Origin/Referer 头）。"""
    api_key: str = Field(..., min_length=8)
    origin: str | None = None


class WidgetSessionResponse(BaseModel):
    session_id: int
    token: str
    expires_in: int
    welcome_text: str | None = None
    # 品牌字段随会话响应下发，widget.js 据此应用租户主题色与 Logo
    theme_color: str | None = None
    logo_url: str | None = None


class WidgetRefundCancelRequest(BaseModel):
    """终端用户撤销售后申请（规格 016）：会话 + 手机号 + 撤销原因（PRD 3.6）。"""
    session_id: int
    phone: str
    reason: str


@router.post("/session", response_model=WidgetSessionResponse, status_code=201)
def create_widget_session(body: WidgetSessionCreateRequest, request: Request, db: Session = Depends(get_db)):
    """公开端点：校验 API Key + 域名白名单 -> 创建匿名会话 -> 返回 Widget 令牌。"""
    from app.services.widget_service import (
        DomainNotAllowed,
        InvalidApiKey,
        TenantUnavailable,
        WidgetError,
        create_widget_session as do_create,
    )

    # Origin 信任顺序 —— 浏览器真实请求头 Origin 优先，其次 Referer，
    # 仅当请求无这两个头时才回退 body.origin（非浏览器客户端的兼容通道）。
    # 避免攻击者用「body 填白名单域名 + 浏览器真实 Origin 是恶意域」绕过域名白名单。
    origin = request.headers.get("Origin") or request.headers.get("Referer") or body.origin
    try:
        return do_create(db, api_key=body.api_key, origin=origin)
    except InvalidApiKey as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except DomainNotAllowed as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except TenantUnavailable as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except WidgetError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{session_id}/message", response_model=SendMessageResponse)
def send_widget_message(
    session_id: int,
    body: SendMessageRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Widget 令牌发消息：令牌会话与路径一致 + 会话归属 + 限流。"""
    tenant_id = _get_tenant_id(request)
    # PRD §3.4 双重限流：Widget 匿名通道补租户级限流（全局中间件对匿名请求跳过）
    from app.services.rate_limit_service import check_tenant_rate_limit
    if not check_tenant_rate_limit(tenant_id)["allowed"]:
        raise HTTPException(status_code=429, detail="租户请求过于频繁，请稍后再试")
    if getattr(request.state, "widget_session_id", None) != session_id:
        raise HTTPException(status_code=403, detail="会话令牌不匹配")

    from app.services.rate_limit_service import check_widget_rate_limit
    if not check_widget_rate_limit(session_id)["allowed"]:
        raise HTTPException(status_code=429, detail="您提问过于频繁，请稍后再试")

    from app.crud import session as session_crud
    s = session_crud.get_by_id(db, session_id, tenant_id)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    from app.services.agent_service import handle_message
    try:
        return handle_message(
            db, session_id=session_id, tenant_id=tenant_id, user_message=body.content,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Agent 处理失败，请稍后重试") from e


@router.post("/refunds/{approval_id}/cancel", status_code=200)
def widget_cancel_refund(
    approval_id: int,
    body: WidgetRefundCancelRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """终端用户撤销自己的退款申请（规格 016）：会话归属 + 手机号后 4 位匹配。"""
    tenant_id = _get_tenant_id(request)
    if getattr(request.state, "widget_session_id", None) != body.session_id:
        raise HTTPException(status_code=403, detail="会话令牌不匹配")

    from app.crud import session as session_crud
    s = session_crud.get_by_id(db, body.session_id, tenant_id)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    from app.services.order_service import OrderError, cancel_refund
    try:
        result = cancel_refund(
            db, approval_id=approval_id, tenant_id=tenant_id, phone=body.phone, reason=body.reason,
        )
        db.commit()
        return result
    except OrderError as e:
        db.rollback()
        msg = str(e)
        code = 409 if "不可撤销" in msg else 400
        raise HTTPException(status_code=code, detail=msg) from e
