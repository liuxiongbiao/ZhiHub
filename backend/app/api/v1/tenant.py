"""
backend/app/api/v1/tenant.py

职责: 租户信息路由 -- 后台首页（任意登录用户）、管理员专属账单接口（角色隔离可测样例）

端点概览:
  GET /home     任意登录用户  -> get_current_user + require_tenant -> TenantHomeResponse
  GET /billing  仅 admin      -> require_role("admin") + require_tenant -> 占位账单

  /billing 用 dependencies=[Depends(require_role("admin"))] 在路由级声明，
  非 admin 角色（如 agent 坐席）访问 -> 403。这是宪法原则 I（中间件判身份、deps 判权限）的落地样例。

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/tenant
  - 下游: app.api.deps（get_current_user / require_role / require_tenant）
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from starlette.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role, require_tenant
from app.core.redis import is_blocklisted
from app.core.security import decode_access_token
from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.tenant import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    CurrentUser,
    InviteRequest,
    InviteResponse,
    RefundThresholdRequest,
    RefundThresholdResponse,
    TenantHomeResponse,
    WidgetConfigQueryResponse,
    WidgetConfigRequest,
    WidgetConfigResponse,
    WidgetDomainsResponse,
    WidgetEmbedCodeResponse,
    WizardStatusResponse,
)
from app.schemas.auth import EnterpriseApproveRequest, EnterpriseApproveResponse
from app.services import tenant_service

router = APIRouter()


class WidgetDomainsRequest(BaseModel):
    """Widget 域名白名单（规格 014）：空列表=仅开发环境 localhost 可用。"""
    domains: list[str] = []


class WidgetApiKeyResponse(BaseModel):
    """Widget API Key 生成响应（规格 014）：原始 Key 只返回一次。"""
    api_key: str


def _map_tenant_error(exc: Exception) -> HTTPException:
    """tenant_service 业务异常 -> HTTP 响应映射。

    集中映射避免每个端点重复 try/except，且保证状态码语义一致。
    """
    if isinstance(exc, tenant_service.TenantNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, tenant_service.WizardAlreadyDone):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, tenant_service.SeatLimitExceeded):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, tenant_service.InvalidInviteToken):
        return HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc))
    if isinstance(exc, tenant_service.TenantNotPending):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    # TenantError 基类（入参校验等）→ 422
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/home", response_model=TenantHomeResponse)
def home(
    request: Request,
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(require_tenant()),
):
    """后台首页: 加载当前用户 + 租户信息 + 告警横幅，返回 TenantHomeResponse。

    告警横幅（规格 008/规格 009）:
      - degradation_alert: 检索基础设施故障降级标记（PRD 3.4/3.8）
      - warning_banner: 配额预警横幅 80%/100%（PRD 3.9），100% 优先于 80%
    """
    # 规格 008: 读降级标记 degradation:{tenant_id}
    degradation_alert = _get_degradation_alert(tenant.id)
    # 规格 009: 读预警横幅 warning_banner:{tenant_id}:{level}，100% 优先
    warning_banner = _get_warning_banner(tenant.id)

    return TenantHomeResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        plan_type=tenant.plan_type,
        status=tenant.status,
        current_user=CurrentUser(user_id=current_user.id, role=request.state.role),
        degradation_alert=degradation_alert,
        warning_banner=warning_banner,
    )


def _get_degradation_alert(tenant_id: int) -> bool:
    """读检索降级标记 degradation:{tenant_id}（PRD 3.4/3.8）。

    retrieval_service 的 vector/bm25 召回失败时设置此标记，TTL 1h。
    Redis 不可用时返回 False（不阻塞首页加载）。
    """
    try:
        from app.core.redis import get_redis
        return get_redis().exists(f"degradation:{tenant_id}") == 1
    except Exception:
        return False


def _get_warning_banner(tenant_id: int) -> str | None:
    """读配额预警横幅 warning_banner:{tenant_id}:{level}（PRD 3.9）。

    billing_service.incr_usage 触发预警时设置，100% 优先于 80%。
    Redis 不可用时返回 None（不阻塞首页加载）。
    """
    try:
        from app.core.redis import get_redis
        r = get_redis()
        # 100% 优先于 80%
        if r.exists(f"warning_banner:{tenant_id}:100"):
            return "100%"
        if r.exists(f"warning_banner:{tenant_id}:80"):
            return "80%"
        return None
    except Exception:
        return None


@router.get("/billing", dependencies=[Depends(require_role("admin"))])
def billing(tenant: Tenant = Depends(require_tenant())):
    """管理员专属账单接口（require_role("admin") 拦截非 admin 角色）。"""
    return {"tenant_id": tenant.id, "plan_type": tenant.plan_type, "usage": {}}


# ────────── 配置向导（PRD §3.1）──────────
@router.get(
    "/wizard",
    response_model=WizardStatusResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def wizard_status(request: Request, db: Session = Depends(get_db)):
    """查询配置向导当前步骤与完成度。仅 admin。"""
    try:
        return WizardStatusResponse(**tenant_service.get_wizard_status(db, request.state.tenant_id))
    except tenant_service.TenantError as e:
        raise _map_tenant_error(e) from e


@router.post(
    "/wizard/next",
    response_model=WizardStatusResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def wizard_advance(request: Request, db: Session = Depends(get_db)):
    """推进配置向导到下一步（有序不可跳步）。仅 admin。done 再推进 -> 409。"""
    try:
        return WizardStatusResponse(**tenant_service.advance_wizard(db, request.state.tenant_id))
    except tenant_service.TenantError as e:
        raise _map_tenant_error(e) from e


@router.post(
    "/invite",
    response_model=InviteResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def invite_member(body: InviteRequest, request: Request, db: Session = Depends(get_db)):
    """admin 邀请坐席：校验 seat_limit -> 生成一次性 token。超 seat_limit -> 409。"""
    try:
        return InviteResponse(
            **tenant_service.invite_member(db, request.state.tenant_id, body.phone)
        )
    except tenant_service.TenantError as e:
        raise _map_tenant_error(e) from e


def _optional_authenticated_user_id(request: Request) -> int | None:
    """公开邀请端点可选解析 JWT；携带无效 JWT 时拒绝而不是降级匿名。"""
    authorization = request.headers.get("Authorization", "")
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效认证信息")
    payload = decode_access_token(authorization[7:].strip())
    if payload is None or (payload.get("jti") and is_blocklisted(payload["jti"])):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效认证信息")
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效认证信息") from exc


@router.post("/invite/accept", response_model=AcceptInviteResponse)
def accept_invite(body: AcceptInviteRequest, request: Request, db: Session = Depends(get_db)):
    """被邀请者接受邀请：校验 token -> 创建 user + member(role=agent)。

    公开端点（被邀请者尚无账号，无 JWT）。token 一次性失效。
    """
    try:
        return AcceptInviteResponse(
            **tenant_service.accept_invite(
                db,
                invite_token=body.token,
                phone=body.phone,
                password=body.password,
                current_user_id=_optional_authenticated_user_id(request),
            )
        )
    except tenant_service.TenantError as e:
        raise _map_tenant_error(e) from e


# ────────── 审计日志查询（PRD §2.1）──────────
@router.get(
    "/audit-logs",
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """审计日志分页查询（PRD 2.1）。仅 admin。

    按 tenant_id 隔离，只返回当前租户的审计记录，按 created_at 倒序。
    审计记录由关键业务操作（退款审批/撤销、登录/登出、向导推进、支付回调）自动写入。
    """
    from app.services.audit_service import list_audit_logs as _list

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
    items, total = _list(db, tenant_id, page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": log.id,
                "tenant_id": log.tenant_id,
                "user_id": log.user_id,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "detail": log.detail,
                "created_at": log.created_at,
            }
            for log in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ────────── 企业版审批（PRD §3.1 #17）──────────
@router.post(
    "/enterprise/approve",
    response_model=EnterpriseApproveResponse,
)
def approve_enterprise(body: EnterpriseApproveRequest, request: Request, db: Session = Depends(get_db)):
    """企业版审批（#17）: admin 审批 pending_review 租户 -> active。

    仅 admin 角色可调用（require_role("admin") 路由级声明）。
    非 pending_review 状态 -> 409，租户不存在 -> 404。
    """
    import secrets as _secrets

    from app.config import settings as _settings
    expected = _settings.platform_ops_token
    supplied = request.headers.get("X-Platform-Token", "")
    if not expected or not _secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅平台运营可执行企业版审批")
    try:
        result = tenant_service.approve_enterprise(db, body.tenant_id)
    except tenant_service.TenantError as e:
        raise _map_tenant_error(e) from e
    return EnterpriseApproveResponse(**result)


# ────────── Widget 品牌定制（PRD §3.8）──────────
@router.post(
    "/widget",
    response_model=WidgetConfigResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def update_widget_config(
    body: WidgetConfigRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """更新 Widget 品牌配置: 主题色/Logo/欢迎语。仅 admin。

    增量更新（仅更新非 None 字段）: 调 tenant_service.update_widget_config（service 内校验+commit）。
    : 主题色/Logo 非法值走 TenantError -> 422 统一映射。
    """
    try:
        return WidgetConfigResponse(
            **tenant_service.update_widget_config(
                db, request.state.tenant_id,
                theme_color=body.theme_color,
                logo_url=body.logo_url,
                welcome_text=body.welcome_text,
            )
        )
    except tenant_service.TenantError as e:
        raise _map_tenant_error(e) from e


@router.get(
    "/widget",
    response_model=WidgetConfigQueryResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def get_widget_config(request: Request, db: Session = Depends(get_db)):
    """: 查询 Widget 完整配置（品牌字段 + 域名白名单）。仅 admin，不回显 API Key。"""
    from app.crud import tenant as tenant_crud

    tenant = tenant_crud.get_by_id(db, request.state.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租户不存在")
    domains = [d for d in (tenant.widget_allowed_domains or "").split(",") if d.strip()]
    return WidgetConfigQueryResponse(
        tenant_id=tenant.id,
        theme_color=tenant.widget_theme_color,
        logo_url=tenant.widget_logo_url,
        welcome_text=tenant.widget_welcome_text,
        domains=domains,
    )


@router.get(
    "/widget/embed-code",
    response_model=WidgetEmbedCodeResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def get_widget_embed_code(
    request: Request,
    db: Session = Depends(get_db),
):
    """获取 Widget 嵌入代码: 可直接粘贴到外部网站的绝对地址 script 片段。仅 admin。

    : script src / API endpoint / WS endpoint 全部使用 PUBLIC_BASE_URL 推导的绝对地址，
    WebSocket 按 scheme 推导（http->ws / https->wss）；生产缺失配置显式 409。
    """
    from app.crud import tenant as tenant_crud
    from app.services.widget_service import (
        PublicBaseUrlMissing,
        html_attr_escape,
        resolve_public_base_url,
        ws_endpoint_for_base,
    )

    tenant_id = request.state.tenant_id
    tenant = tenant_crud.get_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租户不存在")
    try:
        base = resolve_public_base_url()
        ws_endpoint = ws_endpoint_for_base(base)
    except PublicBaseUrlMissing as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    # 属性值统一转义。对历史脏数据先转义再输出（写入侧已校验主题色格式，
    # 这里是防御性兜底，保证任何存储值都无法逃出属性边界）
    theme = html_attr_escape(tenant.widget_theme_color or "#1890ff")
    embed_code = (
        f'<script src="{html_attr_escape(base)}/widget.js" '
        f'data-api-key="PASTE_WIDGET_API_KEY" '
        f'data-endpoint="{html_attr_escape(base)}/api/v1/widget" '
        f'data-ws-endpoint="{html_attr_escape(ws_endpoint)}" '
        f'data-theme-color="{theme}" '
        f'async></script>'
    )
    return WidgetEmbedCodeResponse(tenant_id=tenant.id, embed_code=embed_code)


# ────────── 规格 014 Widget 公共通道：API Key / 域名白名单 ──────────
@router.post(
    "/widget/api-key",
    response_model=WidgetApiKeyResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def rotate_widget_api_key(request: Request, db: Session = Depends(get_db)):
    """生成/轮换 Widget API Key（规格 014）：仅返回一次，旧 Key 立即失效。"""
    try:
        return tenant_service.generate_widget_api_key(db, request.state.tenant_id)
    except tenant_service.TenantNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.put(
    "/widget/domains",
    response_model=WidgetDomainsResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def set_widget_domains(body: WidgetDomainsRequest, request: Request, db: Session = Depends(get_db)):
    """设置 Widget 域名白名单（规格 014）。非法条目（scheme/路径/query 等）-> 422。"""
    try:
        tenant_service.set_widget_allowed_domains(db, request.state.tenant_id, body.domains)
    except tenant_service.TenantError as e:
        raise _map_tenant_error(e) from e
    # 回读租户拿清洗后的域名（service 返回 allowed_domains 键，这里统一 domains 契约）
    from app.crud import tenant as tenant_crud
    tenant = tenant_crud.get_by_id(db, request.state.tenant_id)
    domains = [d for d in (tenant.widget_allowed_domains or "").split(",") if d.strip()]
    return WidgetDomainsResponse(domains=domains)


@router.get(
    "/widget/domains",
    response_model=WidgetDomainsResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def get_widget_domains(request: Request, db: Session = Depends(get_db)):
    """: 查询 Widget 域名白名单。仅 admin。"""
    from app.crud import tenant as tenant_crud

    tenant = tenant_crud.get_by_id(db, request.state.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租户不存在")
    domains = [d for d in (tenant.widget_allowed_domains or "").split(",") if d.strip()]
    return WidgetDomainsResponse(domains=domains)


# ────────── 退款审批阈值（PRD §3.6 规则配置）──────────
@router.get(
    "/refund-threshold",
    response_model=RefundThresholdResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def get_refund_threshold(request: Request, db: Session = Depends(get_db)):
    """查询退款审批阈值（仅 admin）。"""
    try:
        return tenant_service.get_refund_threshold(db, request.state.tenant_id)
    except tenant_service.TenantNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put(
    "/refund-threshold",
    response_model=RefundThresholdResponse,
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def update_refund_threshold(
    body: RefundThresholdRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """更新退款审批阈值（仅 admin）：大额退款（>阈值）需管理员二次审批。"""
    try:
        return tenant_service.update_refund_threshold(
            db, request.state.tenant_id, body.refund_approval_threshold
        )
    except tenant_service.TenantNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except tenant_service.TenantError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get(
    "/export",
    dependencies=[Depends(require_role("admin")), Depends(require_tenant())],
)
def export_tenant_data(request: Request, db: Session = Depends(get_db)):
    """租户核心数据导出（PRD 8.5 最小实现）：CSV 打包 ZIP，仅 admin。"""
    data, filename = tenant_service.export_tenant_data(db, request.state.tenant_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
