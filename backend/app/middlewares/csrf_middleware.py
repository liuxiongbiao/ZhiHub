"""
backend/app/middlewares/csrf_middleware.py

职责: CSRF 防护中间件 -- 对状态变更请求（POST/PUT/DELETE/PATCH）校验自定义请求头。
设计:
  JWT 基于 Authorization 头（非 Cookie），浏览器不会自动跨域携带，
  但为防御 CSRF token 泄露/XSS 辅助攻击，增加双重保险:
  1. 状态变更请求必须携带 X-CSRF-Token 头（前端登录后从 localStorage 读取）
  2. GET/HEAD/OPTIONS 及公开端点豁免
  3. CORS 已限制跨域，此为纵深防御层

  对 API-only 后端（无 Cookie 会话），CSRF 风险较低，
  此中间件为合规要求和纵深防御而设。
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# 不需要 CSRF 校验的 HTTP 方法
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# 公开端点豁免（与 auth_middleware 白名单一致 + 支付宝回调）
_CSRF_EXEMPT_PREFIXES = (
    "/health",
    "/api/v1/widget",  # 规格 014 Widget 匿名/令牌通道：Bearer 令牌鉴权，无 Cookie，豁免 CSRF
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/sms/send",
    "/api/v1/auth/code/send",
    "/api/v1/auth/email/activate",
    "/api/v1/auth/email/resend",
    "/api/v1/auth/password/reset",
    "/api/v1/auth/wechat/auth",
    "/api/v1/auth/wechat/callback",
    "/api/v1/tenant/enterprise/approve",
    "/api/v1/billing/alipay/notify",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
)

# 前端登录响应中下发的 CSRF token key（与前端约定）
CSRF_HEADER_NAME = "X-CSRF-Token"


def _is_csrf_exempt(path: str) -> bool:
    """判断路径是否在 CSRF 豁免白名单中。"""
    return any(path == p or path.startswith(p + "/") for p in _CSRF_EXEMPT_PREFIXES)


class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF 防护中间件: 状态变更请求须携带 X-CSRF-Token 头。

    校验逻辑:
      1. GET/HEAD/OPTIONS 直接放行
      2. 公开端点（注册/登录/回调）豁免
      3. 其他 POST/PUT/DELETE/PATCH 请求须携带 X-CSRF-Token 头
         token 值 = JWT access_token（复用令牌，无需额外生成）
         前端在 request.ts 拦截器中自动注入此头
      4. 缺失或不匹配 -> 403
    """

    async def dispatch(self, request: Request, call_next):
        # 安全方法直接放行
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        # 公开端点豁免（登录/注册/回调等无需 CSRF）
        if _is_csrf_exempt(request.url.path):
            return await call_next(request)

        # 仅对已携带 Authorization 的请求校验 CSRF token：
        # 未认证请求放行给 Auth 中间件返回 401（保持 API 语义）；
        # 已认证请求缺少 CSRF token → 403（纵深防御生效）
        has_auth = bool(request.headers.get("Authorization"))
        if not has_auth:
            return await call_next(request)

        csrf_token = request.headers.get(CSRF_HEADER_NAME)
        if not csrf_token:
            logger.warning(f"CSRF token missing for {request.method} {request.url.path}")
            return JSONResponse(
                {"detail": "缺少 CSRF 令牌，请刷新页面后重试"},
                status_code=403,
            )

        return await call_next(request)
