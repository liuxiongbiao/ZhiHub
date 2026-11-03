"""
backend/app/middlewares/csrf_middleware.py

职责: 对已携带 Bearer 的状态变更请求，要求 X-CSRF-Token 与该 Bearer 完全一致。

这不是浏览器 CSRF 防护。当前登录态在 Authorization 头里，不在 Cookie 中，
跨站页面无法让浏览器自动带上该头。本中间件只拒绝“任意非空头”或缺失头，
避免把存在性检查误写成已完成的 CSRF 控制。引入 Cookie 会话后必须改成
签名双提交，不能沿用“头值等于 Bearer”这一比较。
"""

import hmac
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


def _bearer_token(authorization: str) -> str | None:
    """取出 Bearer 令牌；不是 Bearer 方案时返回 None，交给认证中间件。"""
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):].strip()
    return token or None


def _tokens_match(left: str, right: str) -> bool:
    """常量时间比较两段令牌，长度不同也返回 False。"""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class CSRFMiddleware(BaseHTTPMiddleware):
    """状态变更请求的 Bearer 与 X-CSRF-Token 必须一致。

    校验逻辑:
      1. GET/HEAD/OPTIONS 直接放行
      2. 公开端点（注册/登录/回调）豁免
      3. 未携带 Authorization 的请求交给认证中间件返回 401
      4. 已携带 Bearer 时，X-CSRF-Token 必须与 Bearer 完全一致，否则 403
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        if _is_csrf_exempt(request.url.path):
            return await call_next(request)

        bearer = _bearer_token(request.headers.get("Authorization", ""))
        if bearer is None:
            return await call_next(request)

        csrf_token = request.headers.get(CSRF_HEADER_NAME) or ""
        if not csrf_token or not _tokens_match(csrf_token, bearer):
            logger.warning(f"CSRF token mismatch for {request.method} {request.url.path}")
            return JSONResponse(
                {"detail": "CSRF 令牌与登录令牌不一致"},
                status_code=403,
            )

        return await call_next(request)
