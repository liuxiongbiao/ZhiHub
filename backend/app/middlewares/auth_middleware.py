"""
backend/app/middlewares/auth_middleware.py

职责: JWT 解析 -> 验签/验过期 -> Redis 黑名单查询（登出即时失效） -> 注入 request.state 身份信息

  入站执行顺序: Auth -> Tenant -> RateLimit -> CORS -> 路由
  Auth 必须在 Tenant 之前: Auth 解析 JWT 注入 token_tenant_id/user_id/role，
  Tenant 才能搬运 token_tenant_id -> tenant_id，RateLimit 才能用 tenant_id+user_id 限流。

边界（宪法原则 I）:
  本中间件只判断"是谁"（验签 + 黑名单 + 注入身份），不做角色/租户业务判断（那是 deps.py 的职责）。
  公开路径免鉴权；其余请求无有效令牌或命中黑名单一律 401。

流程:
  1. OPTIONS 预检 -> 直接放行（CORS 在内层接管）
  2. 公开路径（/health、/auth/register、/auth/login、/docs 等）-> 放行
  3. 提取 Bearer token -> 缺失/格式不对 -> 401
  4. decode_access_token 验签 + 验过期 -> 无效 -> 401
  5. 查 Redis 黑名单（jti）-> 命中 -> 401（登出即时失效）
  6. 注入 request.state: user_id / role / jti / token_exp / token_tenant_id
  7. 放行给 Tenant 中间件

依赖关系:
  - 上游: main.py add_middleware(AuthMiddleware)
  - 下游: app.core.security.decode_access_token、app.core.redis.is_blocklisted
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.redis import is_blocklisted
from app.core.security import decode_access_token

logger = logging.getLogger(__name__)

# 免鉴权路径（前缀匹配）: 健康检查、注册、登录、邀请接受（被邀请者无账号）、支付宝异步回调、API 文档
# 支付宝回调由签名校验保证安全（auth_middleware 不持有支付宝公钥，无法验签，故放行交由业务层验签）
# PRD §3.1 账户体系扩展: 验证码发送/登录、邮箱注册/登录、密码找回均为公开端点（用户未登录时使用）
# PRD §3.1 #12 微信扫码登录: /wechat/auth 与 /wechat/callback 公开（扫码前用户未登录），
# /wechat/bind 需 JWT（已登录后才允许绑定）
_PUBLIC_PREFIXES = (
    "/health",
    "/metrics",  # 不走租户 JWT；端点自行校验 X-Platform-Token
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
    "/api/v1/tenant/invite/accept",
    "/api/v1/tenant/enterprise/approve",  # : 平台运营令牌单独鉴权（X-Platform-Token）
    "/api/v1/billing/alipay/notify",
    "/api/v1/widget/session",  # 规格 014: 仅会话创建公开；发消息/撤单需 Widget 令牌
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _is_public(path: str) -> bool:
    """判断路径是否在公开白名单中（前缀匹配）。"""
    return any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    def _unauthorized(self, detail: str = "未认证", status_code: int = 401):
        return JSONResponse({"detail": detail}, status_code=status_code)

    def _authenticated_payload(self, request: Request):
        """返回令牌载荷。缺少令牌、签名失败或黑名单不可用时返回 401 响应。"""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return self._unauthorized()
        payload = decode_access_token(auth_header[len("Bearer "):].strip())
        if payload is None:
            return self._unauthorized()
        jti = payload.get("jti")
        if jti:
            try:
                blocked = is_blocklisted(jti)
            except Exception as exc:
                logger.error(
                    f"Redis unavailable, rejecting token because blocklist cannot be checked: {exc}"
                )
                return self._unauthorized()
            if blocked:
                return self._unauthorized()
        return payload

    def _apply_widget_identity(self, request: Request, payload: dict):
        if not request.url.path.startswith("/api/v1/widget/"):
            return self._unauthorized()
        try:
            request.state.widget_session_id = int(payload.get("session_id"))
        except (TypeError, ValueError):
            return self._unauthorized("无效令牌")
        request.state.user_id = None
        request.state.role = "widget"
        request.state.jti = payload.get("jti")
        request.state.token_exp = payload.get("exp")
        request.state.token_tenant_id = payload.get("tenant_id")
        return None

    def _apply_user_identity(self, request: Request, payload: dict):
        try:
            request.state.user_id = int(payload["sub"])
        except (KeyError, ValueError, TypeError):
            return self._unauthorized("无效令牌")
        request.state.role = payload.get("role")
        request.state.jti = payload.get("jti")
        request.state.token_exp = payload.get("exp")
        request.state.token_tenant_id = payload.get("tenant_id")
        return None

    async def dispatch(self, request: Request, call_next):
        # 放行 CORS 预检（不带 Authorization，实际请求仍走鉴权）
        # 避免 Auth 在 CORSMiddleware 外层时把 preflight OPTIONS 拦成 401
        if request.method == "OPTIONS" or _is_public(request.url.path):
            return await call_next(request)

        payload = self._authenticated_payload(request)
        if isinstance(payload, JSONResponse):
            return payload
        if payload.get("role") == "widget":
            rejected = self._apply_widget_identity(request, payload)
        else:
            rejected = self._apply_user_identity(request, payload)
        if rejected is not None:
            return rejected
        return await call_next(request)
