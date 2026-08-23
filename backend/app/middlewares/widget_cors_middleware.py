"""
backend/app/middlewares/widget_cors_middleware.py

职责: Widget 公共通道动态 CORS -- 外部客户网站嵌入 widget.js 后，浏览器对
      /api/v1/widget/* 的跨域请求/预检按「租户 Widget 域名白名单」放行。

核心设计:
  - 不全局放开 CORS: 全局 CORSMiddleware 仍只允许管理后台 origin（cors_origins）。
    本中间件仅处理 /api/v1/widget/* 前缀，且只有 Origin 的 host 命中任一租户的
    widget_allowed_domains（精确或子域名）才回 Access-Control-Allow-Origin。
  - 白名单缓存: 全量租户域名聚合做 60s 进程内 TTL 缓存，避免每个预检都打 DB；
    DB 故障时回退缓存旧值/空集（fail-closed，不放行未知来源）。
  - 开发/测试环境额外放行 localhost 来源（与 widget_service.is_domain_allowed 行为一致），
    便于本地与 E2E 验证。
  - OPTIONS 预检直接短路 204，不进入后续中间件链（预检不携带鉴权头）。

关键技术点:
  - 注册为最外层（最后 add_middleware）: 预检在 Auth/Tenant/CSRF 之前被应答。
  - Vary: Origin -- 响应随 Origin 变化，防止共享缓存串用 CORS 头。
  - allow-credentials 不开: Widget 匿名通道用 Bearer Widget token，不用 cookie。

依赖关系:
  - 上游: app.main（注册）
  - 下游: app.core.database（读租户白名单）、app.config
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

WIDGET_PATH_PREFIX = "/api/v1/widget"
_WHITELIST_CACHE_TTL = 60.0

# 进程内白名单缓存: {"ts": 加载时间戳, "hosts": frozenset}
_hosts_cache: dict = {"ts": 0.0, "hosts": frozenset()}


def _load_hosts_from_db() -> frozenset[str]:
    """聚合全租户 Widget 域名白名单为 host 集合（DB 故障回退空集）。"""
    try:
        from app.core.database import SessionLocal
        from app.models.tenant import Tenant
        from sqlalchemy import select

        db = SessionLocal()
        try:
            rows = db.execute(select(Tenant.widget_allowed_domains)).scalars().all()
        finally:
            db.close()
        hosts: set[str] = set()
        for raw in rows:
            for entry in (raw or "").split(","):
                entry = entry.strip().lower()
                if entry:
                    hosts.add(entry)
        return frozenset(hosts)
    except Exception as exc:  # noqa: BLE001 fail-closed：DB 不可用不放行未知来源
        logger.warning(f"widget cors whitelist load failed: {exc}")
        return frozenset()


def _allowed_hosts() -> frozenset[str]:
    """带 TTL 的白名单读取。"""
    now = time.monotonic()
    if now - _hosts_cache["ts"] > _WHITELIST_CACHE_TTL:
        _hosts_cache["hosts"] = _load_hosts_from_db()
        _hosts_cache["ts"] = now
    return _hosts_cache["hosts"]


def _origin_allowed(host: str) -> bool:
    """Origin host 是否可放行: 租户白名单（精确/子域）或开发环境 localhost。"""
    from app.config import settings

    if settings.app_env in ("development", "test"):
        if host in ("localhost", "127.0.0.1") or host.endswith(".localhost"):
            return True
    for entry in _allowed_hosts():
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


class WidgetCorsMiddleware(BaseHTTPMiddleware):
    """仅对 /api/v1/widget/* 按租户白名单动态放行 CORS。

    : Origin 可能同时命中全局 CORSMiddleware（cors_origins）与本中间件的
    租户白名单。单值头（Allow-Origin/Methods/Max-Age）必须替换而非 append，
    避免响应出现两条 Access-Control-Allow-Origin 被浏览器判为非法；Vary 只合并
    一次 Origin，不覆盖已有 Vary 项。
    """

    @staticmethod
    def _merge_cors_headers(response: Response, origin: str) -> None:
        single_value = {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Max-Age": "600",
        }
        for key, value in single_value.items():
            response.headers[key] = value  # 赋值语义：替换同名头，不追加
        # Widget 匿名通道走 Bearer token，不允许凭据模式。
        # 显式删除全局 CORSMiddleware（allow_credentials=True）遗留的头，
        # 避免浏览器尝试发送 cookie 或把响应判为非法。
        if "access-control-allow-credentials" in response.headers:
            del response.headers["access-control-allow-credentials"]
        # Vary: 合并（逗号分隔），只补一次 Origin
        existing = response.headers.get("vary") or ""
        parts = [p.strip().lower() for p in existing.split(",") if p.strip()]
        if "origin" not in parts:
            parts.append("origin")
            response.headers["vary"] = ", ".join(parts)

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin") or ""
        if not origin or not request.url.path.startswith(WIDGET_PATH_PREFIX):
            return await call_next(request)

        host = (urlparse(origin).hostname or "").lower()
        if not host or not _origin_allowed(host):
            # 未知来源: 不加 CORS 头（浏览器拦截响应），请求本身仍交由后续链路
            # 按正常业务校验（API Key/域名白名单会在业务层再拦一道）
            return await call_next(request)

        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers(origin))

        response = await call_next(request)
        self._merge_cors_headers(response, origin)
        return response
