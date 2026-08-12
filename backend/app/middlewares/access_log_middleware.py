"""
backend/app/middlewares/access_log_middleware.py

职责: 为每个请求生成/透传 trace_id，记录方法/路径/状态码/耗时访问日志

中间件链位置:
  入站执行顺序: AccessLog -> Auth -> Tenant -> RateLimit -> CORS -> 路由
  AccessLog 在最外层（最后注册），先注入 trace_id 再进 Auth，记录所有请求（含被 401 拒绝的）耗时。

核心设计:
  优先透传客户端 X-Request-Id（链路追踪），无则生成 12 位 hex。trace_id 注入 request.state
  供下游日志关联，并回写到响应头 X-Request-Id 便于客户端排查。dispatch 计时包裹 call_next，
  记录 method/path/status/耗时。

依赖关系:
  - 上游: main.py add_middleware(AccessLogMiddleware)（最外层）
  - 下游: 无（仅日志 + header 注入）
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # trace_id：优先透传客户端 X-Request-Id（链路追踪），无则生成
        trace_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        request.state.trace_id = trace_id

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # 回写响应头，便于客户端关联日志
        response.headers["X-Request-Id"] = trace_id
        logger.info(
            f"{request.method} {request.url.path} {response.status_code} "
            f"{elapsed_ms}ms trace={trace_id}"
        )
        return response
