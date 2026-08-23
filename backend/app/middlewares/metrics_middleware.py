"""app/middlewares/metrics_middleware.py

Prometheus HTTP 指标中间件（企业标准监控探针，审计复核补齐）。
记录每个请求的方法/路径/状态码计数与耗时直方图，供 /metrics 抓取。
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core import metrics


def _normalize_path(request: Request) -> str:
    """归一化路径标签：优先使用路由模板（{param}），防止基数爆炸。"""
    route = request.scope.get("route")
    if route and hasattr(route, "path_format"):
        return route.path_format
    return request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        path = _normalize_path(request)
        try:
            response = await call_next(request)
        except Exception:
            # 异常请求也计入 500，避免探针丢失故障样本
            metrics.HTTP_REQUESTS.labels(
                method=request.method, path=path, status="500"
            ).inc()
            metrics.HTTP_LATENCY.labels(
                method=request.method, path=path
            ).observe(time.perf_counter() - start)
            raise
        metrics.HTTP_REQUESTS.labels(
            method=request.method, path=path, status=str(response.status_code)
        ).inc()
        metrics.HTTP_LATENCY.labels(
            method=request.method, path=path
        ).observe(time.perf_counter() - start)
        return response
