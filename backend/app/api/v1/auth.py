"""
backend/app/api/v1/auth.py

职责: 认证路由 -- 注册（手机号/邮箱）、登录（密码/验证码）、登出、验证码发送、密码找回、绑定邮箱
对应日程: Day 2 / PRD 3.1 账户体系扩展

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

from app.api.deps import get_current_user
from app.core import redis as redis_module
from app.core.database import get_db
from app.models.user import User
from app.schemas.auth import (
    BindEmailRequest,
    EmailLoginRequest,
    EmailRegisterRequest,
    EmailRegisterResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    RegisterRequest,
    RegisterResponse,
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
    EmailAlreadyExists,
    InvalidCode,
    InvalidCredentials,
    PhoneAlreadyExists,
    RateLimitExceeded,
    WechatBindConflict,
    WechatStateError,
    WeakPassword,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _enforce_ip_rate_limit(request: Request) -> str | None:
    """登录端点 IP 频率限流（T16/M3）：30/min 超限 -> 429；Redis 故障 fail-open 放行（M1，与 T13 一致）。

    三个登录端点（/login、/login/sms、/login/email）共用，防任一端点被无限速暴力破解。
    返回客户端 IP（供 auth_service.login 做 IP 维度失败计数；其他登录方式忽略返回值）。
    """
    client_ip = request.client.host if request.client else None
    if not client_ip:
        return None
    try:
        allowed = redis_module.check_ip_rate_limit(client_ip)
    except Exception as e:
        # Redis 故障 fail-open：限流暂失但不阻断登录（与 auth/rate_limit 中间件一致）
        logger.warning(f"Redis unavailable, login IP rate limit skipped: {e}")
        return client_ip
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求过于频繁，请稍后再试")
    return client_ip


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

    T16: 登录端点 IP 频率限流（30/min）超限 -> 429；
    失败计数同时累计 phone 与 IP 维度（防刷锁他人账号）。
    """
    client_ip = _enforce_ip_rate_limit(request)
    try:
        result = auth_service.login(
            db, phone=body.phone, password=body.password, ip=client_ip,
        )
    except AccountLocked as e:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(e)) from e
    except InvalidCredentials as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    return LoginResponse(
        access_token=result.access_token,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
        phone=result.phone,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, db: Session = Depends(get_db)):
    """登出: 从 request.state 取 jti/exp，调 auth_service.logout 写入 Redis 黑名单 + 审计日志。"""
    # jti/exp 由 auth_middleware 从令牌解析并注入 request.state
    jti = getattr(request.state, "jti", None)
    exp = getattr(request.state, "token_exp", None)
    if not jti or exp is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
    # 007: 传 user_id/tenant_id/role 用于从在线坐席集合移除
    # PRD 2.1: 传 db 用于写审计日志
    auth_service.logout(
        db,
        jti=jti, exp=int(exp),
        user_id=getattr(request.state, "user_id", None),
        tenant_id=getattr(request.state, "tenant_id", None),
        role=getattr(request.state, "role", None),
    )
    return MessageResponse(message="已登出")


# --- 账户体系扩展（PRD 3.1 #10-#17） ---

@router.post("/sms/send", response_model=SendCodeResponse)
def send_sms_code(body: SendCodeRequest):
    """发送短信验证码（#10）: demo 模式返回 code，频率超限 -> 429。"""
    try:
        result = auth_service.send_verification_code(body.phone, code_type="sms")
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return SendCodeResponse(
        message=result.message,
        code=result.code,
        expires_in=result.expires_in,
    )


@router.post("/code/send", response_model=SendCodeResponse)
def send_generic_code(body: SendGenericCodeRequest):
    """通用验证码发送（013 个人中心）: 支持 sms / email 两类验证码。
    复用 send_verification_code（频率限制 1min/次、24h/10次），demo 返回 code 供测试。
    """
    try:
        result = auth_service.send_verification_code(
            body.target, code_type=body.code_type
        )
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except WeakPassword as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return SendCodeResponse(
        message=result.message,
        code=result.code,
        expires_in=result.expires_in,
    )


@router.post("/login/sms", response_model=LoginResponse)
def login_by_sms(body: SmsLoginRequest, request: Request, db: Session = Depends(get_db)):
    """验证码登录（#10）: 验证码无效或用户不存在 -> 401。M3: IP 限流同 /login。"""
    _enforce_ip_rate_limit(request)
    try:
        result = auth_service.login_by_code(db, phone=body.phone, code=body.code)
    except InvalidCode as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    return LoginResponse(
        access_token=result.access_token,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
        phone=result.phone,
    )


@router.post("/register/email", response_model=EmailRegisterResponse, status_code=status.HTTP_201_CREATED)
def register_by_email(body: EmailRegisterRequest, db: Session = Depends(get_db)):
    """邮箱注册（#11）: 邮箱重复 -> 409，密码弱/邮箱格式错 -> 422。"""
    try:
        result = auth_service.register_by_email(
            db, email=body.email, password=body.password,
            tenant_name=body.tenant_name, plan_type=body.plan_type,
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
    )


@router.post("/login/email", response_model=LoginResponse)
def login_by_email(body: EmailLoginRequest, request: Request, db: Session = Depends(get_db)):
    """邮箱密码登录（#11）: 凭据无效 -> 401。M3: IP 限流防暴力破解（此前无任何防护）。"""
    _enforce_ip_rate_limit(request)
    try:
        result = auth_service.login_by_email(db, email=body.email, password=body.password)
    except InvalidCredentials as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
    return LoginResponse(
        access_token=result.access_token,
        user_id=result.user_id,
        tenant_id=result.tenant_id,
        role=result.role,
        phone=result.phone,
    )


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


# --- 微信扫码登录（PRD 3.1 #12） ---

@router.get("/wechat/auth", response_model=WechatAuthResponse)
def wechat_auth(redirect_uri: str):
    """微信授权 URL（#12）: 公开免鉴权，返回授权 URL 给前端跳转。

    T12: state 由服务端生成（内嵌 auth_url，回调带回校验），不再信任客户端传入。
    """
    result = auth_service.wechat_login_start(redirect_uri=redirect_uri)
    return WechatAuthResponse(auth_url=result["auth_url"])


@router.get("/wechat/callback", response_model=WechatCallbackResponse)
def wechat_callback(code: str, state: str | None = None, db: Session = Depends(get_db)):
    """微信回调（#12）: 公开免鉴权，校验 state 后 code 换 openid -> 登录/绑定引导。

    T12: state 必须由 wechat_auth 签发（Redis 一次性校验，防 OAuth CSRF）；
    缺失/伪造统一由服务层抛 WechatStateError -> 400（不用 FastAPI 422 区分缺失与伪造，防探测）。
    """
    try:
        result = auth_service.wechat_login_callback(db, code=code, state=state)
    except WechatStateError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return WechatCallbackResponse(
        need_bind=result.need_bind,
        openid=result.openid,
        access_token=result.access_token,
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
