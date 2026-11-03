"""
backend/app/api/v1/auth.py

职责: 认证路由 -- 注册（手机号/邮箱）、登录（密码/验证码）、登出、验证码发送、密码找回、绑定邮箱

端点概览:
  POST /register         201  手机号注册（公开）       -> auth_service.register
  POST /login            200  手机号密码登录（公开）   -> auth_service.login
  POST /logout            200  登出（需登录）           -> auth_service.logout
  POST /sms/send          200  发送短信验证码（公开）   -> auth_service.send_verification_code（#10）
  POST /login/sms         200  验证码登录（公开）       -> auth_service.login_by_code（#10）
  POST /register/email    201  邮箱注册（公开）         -> auth_service.register_by_email（#11）
  POST /login/email       200  邮箱密码登录（公开）     -> auth_service.login_by_email（#11）
  POST /password/reset    200  密码找回（公开）         -> auth_service.reset_password（#16）
  POST /bind/email        200  绑定邮箱（需登录）       -> auth_service.bind_email（#15）

异常映射:
  PhoneAlreadyExists/EmailAlreadyExists -> 409 | WeakPassword -> 422
  InvalidCredentials/InvalidCode -> 401 | AccountLocked -> 423
  RateLimitExceeded -> 429

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/auth
  - 下游: app.services.auth_service
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

import logging

from app.api.deps import get_current_user, require_role
from app.config import settings
from app.core import redis as redis_module
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import (
    BindPhoneRequest,
    BindEmailRequest,
    EmailLoginRequest,
    EmailRegisterRequest,
    EmailRegisterResponse,
    EmailActivateRequest,
    EmailActivateResponse,
    EmailResendRequest,
    EmailResendResponse,
    DemoLoginRequest,
    WsTokenResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RefreshResponse,
    RevokeUserTokensRequest,
    RegisterRequest,
    RegisterResponse,
    PublicCapabilitiesResponse,
    ResetPasswordRequest,
    SendCodeRequest,
    SendCodeResponse,
    SendGenericCodeRequest,
    SmsLoginRequest,
    WechatAuthResponse,
    WechatBindRequest,
    WechatBindResponse,
    WechatCallbackResponse,
)
from app.services import auth_service
from app.services.auth_service import (
    AccountLocked,
    AuthStoreUnavailable,
    EmailAlreadyExists,
    EmailNotVerified,
    InvalidCode,
    InvalidCredentials,
    InvalidActivationToken,
    InvalidRefreshToken,
    PhoneAlreadyExists,
    RateLimitExceeded,
    SmsDeliveryError,
    WechatBindConflict,
    WechatStateError,
    WeakPassword,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _enforce_ip_rate_limit(request: Request) -> str | None:
    """登录端点 IP 频率限流：30/min 超限 -> 429；Redis 故障 fail-open 放行（与一致）。

    三个登录端点（/login、/login/sms、/login/email）共用，防任一端点被无限速暴力破解。
    返回客户端 IP（供 auth_service.login 做 IP 维度失败计数；其他登录方式忽略返回值）。
    """
    client_ip = request.client.host if request.client else None
    if not client_ip:
        return None
    try:
        allowed = redis_module.check_ip_rate_limit(client_ip)
    except Exception as e:
        # 登录频率限制失败即放行，避免 Redis 抖动时无法登录。账号锁定查询仍会拒绝。
        logger.warning(
            "Redis unavailable, login IP rate limit skipped by design so an outage does not block sign-in: %s",
            e,
        )
        return client_ip
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求过于频繁，请稍后再试")
    return client_ip


def _should_reveal_code(request: Request) -> bool:
    """: 验证码回显策略 —— test 固定回显；development 仅回环来源回显；生产不回显。"""
    if settings.app_env == "test":
        return True
    if settings.app_env == "development":
        host = request.client.host if request.client else ""
        return host in ("127.0.0.1", "::1", "localhost") or settings.dev_allow_code_reveal
    return False


@router.get("/login/options", response_model=PublicCapabilitiesResponse)
def login_options(db: Session = Depends(get_db)):
    """返回登录页可展示的能力状态，不泄露任何第三方凭据。"""
    from app.models.tenant import Tenant

    demo_available = bool(
        settings.demo_quick_login_enabled
        and settings.app_env in {"development", "test", "demo"}
        and db.query(Tenant.id).filter(
            Tenant.is_demo.is_(True), Tenant.status == "active"
        ).first()
    )
    return PublicCapabilitiesResponse(
        email_login=settings.email_enabled or settings.app_env != "production",
        sms_login=settings.sms_enabled,
        wechat_login=settings.wechat_enabled,
        payment=settings.payment_enabled,
        demo_quick_login=demo_available,
        demo_personas=list(auth_service.DEMO_PERSONA_PHONES) if demo_available else [],
    )


@router.post("/login/demo", response_model=LoginResponse)
def login_demo(
    body: DemoLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """开发/演示环境角色快速登录；生产环境和未显式开启时始终拒绝。"""
    _enforce_ip_rate_limit(request)
    try:
        result = auth_service.demo_quick_login(db, persona=body.persona)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=404, detail="演示快速登录不可用") from exc
    return LoginResponse(**result.__dict__)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """注册: 调 auth_service.register，手机号重复 -> 409，密码弱 -> 422。"""
    try:
        result = auth_service.register(
            db, phone=body.phone, password=body.password, tenant_name=body.tenant_name,
            code=body.code,
        )
    except PhoneAlreadyExists as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except InvalidCode as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(
            status_code=422, detail=str(e)
        ) from e
    return RegisterResponse(
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        phone=result.phone,
        role=result.role,
        plan_type=result.plan_type,
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """登录: 调 auth_service.login，凭据无效 -> 401，账户锁定 -> 423。

    : 登录端点 IP 频率限流（30/min）超限 -> 429；
    失败计数同时累计 phone 与 IP 维度（防刷锁他人账号）。
    """
    client_ip = _enforce_ip_rate_limit(request)
    try:
        result = auth_service.login(
            db, phone=body.phone, password=body.password, ip=client_ip,
        )
    except AccountLocked as e:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(e)) from e
    except AuthStoreUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except InvalidCredentials as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    return LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
        phone=result.phone,
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    """用一次性 refresh token 轮换新 token 对；旧值复用会撤销家族。"""
    try:
        result = auth_service.refresh(db, refresh_token=body.refresh_token)
    except InvalidRefreshToken as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    return RefreshResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    body: LogoutRequest | None = None,
    db: Session = Depends(get_db),
):
    """登出: 从 request.state 取 jti/exp，调 auth_service.logout 写入 Redis 黑名单 + 审计日志。"""
    # jti/exp 由 auth_middleware 从令牌解析并注入 request.state
    jti = getattr(request.state, "jti", None)
    exp = getattr(request.state, "token_exp", None)
    if not jti or exp is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
    # 规格 007: 传 user_id/tenant_id/role 用于从在线坐席集合移除
    # PRD §2.1: 传 db 用于写审计日志
    auth_service.logout(
        db,
        jti=jti, exp=int(exp),
        user_id=getattr(request.state, "user_id", None),
        tenant_id=getattr(request.state, "tenant_id", None),
        role=getattr(request.state, "role", None),
    )
    if body and body.refresh_token:
        auth_service.revoke_refresh_token(db, refresh_token=body.refresh_token)
    return MessageResponse(message="已登出")


@router.post(
    "/tokens/revoke-user",
    response_model=MessageResponse,
    dependencies=[Depends(require_role("admin"))],
)
def revoke_user_tokens(
    body: RevokeUserTokensRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """管理员踢出：撤销指定用户所有 refresh token。"""
    auth_service.revoke_user_refresh_tokens(
        db, user_id=body.user_id, tenant_id=request.state.tenant_id
    )
    return MessageResponse(message="用户已踢出")


# ——- 账户体系扩展（PRD §3.1 #10-#17） ——-

@router.post("/ws-token", response_model=WsTokenResponse)
def create_ws_token(request: Request):
    """坐席工作台 WebSocket 短效令牌：1 分钟有效，仅 agent/admin 可申请。"""
    role = getattr(request.state, "role", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)
    if role not in ("agent", "admin") or tenant_id is None or user_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅坐席/管理员可获取工作台令牌")
    token = create_access_token(
        subject=str(user_id),
        extra_claims={"tenant_id": tenant_id, "role": role, "ws": True},
        expires_minutes=1,
    )
    return WsTokenResponse(token=token, expires_in=60)


@router.post("/sms/send", response_model=SendCodeResponse)
def send_sms_code(body: SendCodeRequest, request: Request):
    """发送短信验证码（#10）: demo 模式返回 code，频率超限 -> 429。"""
    try:
        result = auth_service.send_verification_code(
            body.phone, code_type="sms", reveal_code=_should_reveal_code(request)
        )
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except SmsDeliveryError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return SendCodeResponse(
        message=result.message,
        code=result.code,
        expires_in=result.expires_in,
    )


@router.post("/code/send", response_model=SendCodeResponse)
def send_generic_code(body: SendGenericCodeRequest, request: Request):
    """通用验证码发送（规格 013 个人中心）: 支持 sms / email 两类验证码。
    复用 send_verification_code（频率限制 1min/次、24h/10次），demo 返回 code 供测试。
    """
    try:
        result = auth_service.send_verification_code(
            body.target, code_type=body.code_type, reveal_code=_should_reveal_code(request)
        )
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except SmsDeliveryError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return SendCodeResponse(
        message=result.message,
        code=result.code,
        expires_in=result.expires_in,
    )


@router.post("/login/sms", response_model=LoginResponse)
def login_by_sms(body: SmsLoginRequest, request: Request, db: Session = Depends(get_db)):
    """验证码登录（#10）: 验证码无效或用户不存在 -> 401。: IP 限流同 /login。"""
    _enforce_ip_rate_limit(request)
    try:
        result = auth_service.login_by_code(db, phone=body.phone, code=body.code)
    except InvalidCode as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    return LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
        phone=result.phone,
    )


@router.post("/register/email", response_model=EmailRegisterResponse, status_code=status.HTTP_201_CREATED)
def register_by_email(body: EmailRegisterRequest, request: Request, db: Session = Depends(get_db)):
    """邮箱注册（#11）: 邮箱重复 -> 409，密码弱/邮箱格式错 -> 422。"""
    try:
        result = auth_service.register_by_email(
            db, email=body.email, password=body.password,
            tenant_name=body.tenant_name, plan_type=body.plan_type,
            activation_link_revealed=_should_reveal_code(request),
        )
    except EmailAlreadyExists as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return EmailRegisterResponse(
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        email=result.email,
        role=result.role,
        plan_type=result.plan_type,
        email_verified=result.email_verified,
        activation_link=result.activation_link,
    )


@router.post("/login/email", response_model=LoginResponse)
def login_by_email(body: EmailLoginRequest, request: Request, db: Session = Depends(get_db)):
    """邮箱密码登录（#11）: 凭据无效 -> 401。: IP 限流防暴力破解（此前无任何防护）。"""
    _enforce_ip_rate_limit(request)
    try:
        result = auth_service.login_by_email(db, email=body.email, password=body.password)
    except InvalidCredentials as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except EmailNotVerified as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    return LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
        phone=result.phone,
    )


@router.post("/email/activate", response_model=EmailActivateResponse)
def activate_email(body: EmailActivateRequest, db: Session = Depends(get_db)):
    """邮箱激活：校验一次性激活 token，标记账号已验证。"""
    try:
        return auth_service.activate_email(db, token=body.token)
    except InvalidActivationToken as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/email/resend", response_model=EmailResendResponse)
def resend_email_activation(body: EmailResendRequest, db: Session = Depends(get_db)):
    """重新发送激活邮件（60s 限频；不存在的邮箱也返回成功，防枚举）。"""
    try:
        return auth_service.resend_email_activation(db, email=body.email)
    except RateLimitExceeded as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e


@router.post("/password/reset", response_model=MessageResponse)
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """密码找回（#16）: 验证码验证身份 -> 重置密码。验证码无效 -> 401，新密码弱 -> 422。"""
    try:
        auth_service.reset_password(
            db, target=body.target, code=body.code,
            new_password=body.new_password, code_type=body.code_type,
        )
    except InvalidCode as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return MessageResponse(message="密码已重置")


@router.post("/bind/email", response_model=MessageResponse)
def bind_email(
    body: BindEmailRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """绑定邮箱（#15）: 需登录。验证码无效 -> 401，邮箱已占用 -> 409。"""
    try:
        auth_service.bind_email(
            db, user_id=current_user.id, email=body.email, code=body.code
        )
    except InvalidCode as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except EmailAlreadyExists as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return MessageResponse(message="邮箱绑定成功")


@router.post("/bind/phone", response_model=MessageResponse)
def bind_phone(
    body: BindPhoneRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """绑定手机号（PRD 3.1）: 需登录。验证码无效 -> 401，手机号已占用 -> 409。"""
    try:
        auth_service.bind_phone(
            db, user_id=current_user.id, phone=body.phone, code=body.code
        )
    except InvalidCode as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    except PhoneAlreadyExists as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return MessageResponse(message="手机号绑定成功")


# ——- 微信扫码登录（PRD §3.1 #12） ——-

@router.get("/wechat/auth", response_model=WechatAuthResponse)
def wechat_auth(redirect_uri: str):
    """微信授权 URL（#12）: 公开免鉴权，返回授权 URL 给前端跳转。

    : state 由服务端生成（内嵌 auth_url，回调带回校验），不再信任客户端传入。
    """
    result = auth_service.wechat_login_start(redirect_uri=redirect_uri)
    return WechatAuthResponse(auth_url=result["auth_url"])


@router.get("/wechat/callback", response_model=WechatCallbackResponse)
def wechat_callback(code: str, state: str | None = None, db: Session = Depends(get_db)):
    """微信回调（#12）: 公开免鉴权，校验 state 后 code 换 openid -> 登录/绑定引导。

    : state 必须由 wechat_auth 签发（Redis 一次性校验，防 OAuth CSRF）；
    缺失/伪造统一由服务层抛 WechatStateError -> 400（不用 FastAPI 422 区分缺失与伪造，防探测）。
    """
    try:
        result = auth_service.wechat_login_callback(db, code=code, state=state)
    except WechatStateError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except RuntimeError as e:
        # 微信 API 失败（code 无效/过期/网络错误）：返回 502 而非 500，
        # 避免内部异常暴露给前端，同时让调用方知道是微信侧问题
        logger.warning(f"WeChat callback failed: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="微信授权失败，请重新扫码") from e
    return WechatCallbackResponse(
        need_bind=result.need_bind,
        openid=result.openid,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
    )


@router.post("/wechat/bind", response_model=WechatBindResponse)
def wechat_bind(
    body: WechatBindRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """微信绑定（#12）: 需登录，openid 已被他人绑定 -> 409。"""
    try:
        openid = auth_service.wechat_bind(db, user_id=current_user.id, code=body.code)
    except WechatBindConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return WechatBindResponse(message="微信绑定成功", openid=openid)
