"""
backend/app/middlewares/rate_limit_middleware.py

职责: 基于 tenant_id + user_id 双维度令牌桶限流

  入站顺序 Auth -> Tenant -> RateLimit
  RateLimit 在最后: 依赖 Auth 注入的 user_id 和 Tenant 注入的 tenant_id。
  若在前两者之前，tenant_id/user_id 为 None -> 降级放行 -> 限流失效。

限流策略:
  - 租户维度: 100 req/min
  - 用户维度: 20 req/min
  - 超限 -> 429 Too Many Requests
  - 未认证请求放行（由 Auth 中间件拦截）
  - 白名单路径（/health、/auth/login、/auth/register 等）不限流

依赖关系:
  - 上游: main.py add_middleware(RateLimitMiddleware)
  - 下游: app.services.rate_limit_service.check_rate_limit（Redis 令牌桶）
"""

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.services.rate_limit_service import check_rate_limit

logger = logging.getLogger(__name__)

# 不限流的白名单路径
SKIP_PATHS = {"/health", "/api/v1/auth/login", "/api/v1/auth/register", "/docs", "/openapi.json"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """双维度限流中间件: 租户 100 req/min + 用户 20 req/min，超限 -> 429。"""

    async def dispatch(self, request: Request, call_next):
        # 1. 跳过白名单路径
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        # 读请求不参与全局写限流 —— 后台导航/通知轮询是正常负载，
        # 按 20 req/min 的用户配额会把正常使用打成 429；写请求继续受双维限流。
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        # 2. 提取租户 + 用户身份（未认证则放行，由 Auth 中间件拦截）
        tenant_id = getattr(request.state, "tenant_id", None)
        user_id = getattr(request.state, "user_id", None)
        if tenant_id is None or user_id is None:
            return await call_next(request)

        # 3. 双维度令牌桶校验（Redis 故障降级放行，）
        try:
            result = check_rate_limit(tenant_id, user_id)
        except Exception as e:
            # 限流依赖 Redis；故障时 fail-open（可用性优先），告警日志。
            # 代价: 限流保护在故障期暂失，但 Auth 中间件仍拦截未认证请求。
            logger.warning(f"Redis unavailable, rate limit check skipped: {e}")
            return await call_next(request)
        if not result["allowed"]:
            return JSONResponse(
                {"detail": f"请求过于频繁 (租户:{result['tenant_count']} 用户:{result['user_count']})"},
                status_code=429,
            )
        return await call_next(request)
