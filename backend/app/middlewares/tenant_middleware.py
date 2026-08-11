"""
backend/app/middlewares/tenant_middleware.py

职责: 从 JWT claims 搬运 tenant_id 到 request.state.tenant_id，确保下游查询统一携带租户过滤
对应日程: Day 2

中间件链位置（面试点）:
  入站顺序 Auth -> Tenant -> RateLimit
  Tenant 在 Auth 之后: 读取 Auth 注入的 request.state.token_tenant_id，搬运为 request.state.tenant_id。
  下游所有 CRUD/Service 统一从 request.state.tenant_id 取租户上下文。

红线（宪法原则 II）:
  tenant_id 只能来自可信来源（JWT 令牌）。绝不读取请求体/查询参数中客户端自报的 tenant_id。
  客户端可伪造 body/query 中的 tenant_id，本中间件绝不读取那些来源。

流程:
  1. 从 request.state 读 token_tenant_id（Auth 中间件注入）
  2. 存在 -> 注入 request.state.tenant_id
  3. 不存在（公开路径，未经 Auth）-> 跳过，不注入租户上下文
  4. 放行

依赖关系:
  - 上游: main.py add_middleware(TenantMiddleware)
  - 下游: 无（纯搬运，不查库不做业务判断）
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 仅当 Auth 中间件已注入身份时，搬运令牌中的 tenant_id
        # （公开路径没有 token_tenant_id，保持无租户上下文）
        token_tenant_id = getattr(request.state, "token_tenant_id", None)
        if token_tenant_id is not None:
            request.state.tenant_id = token_tenant_id
        return await call_next(request)
