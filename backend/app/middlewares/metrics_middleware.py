"""app/middlewares/metrics_middleware.py

Prometheus HTTP 指标中间件（企业标准监控探针，审查 P2 补齐）。
记录每个请求的方法/路径/状态码计数与耗时直方图，供 /metrics 抓取。
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.core import metrics


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 异常请求也计入 500，避免探针丢失故障样本
            metrics.HTTP_REQUESTS.labels(
                method=request.method, path=request.url.path, status="500"
            ).inc()
            metrics.HTTP_LATENCY.labels(
                method=request.method, path=request.url.path
            ).observe(time.perf_counter() - start)
            raise
        metrics.HTTP_REQUESTS.labels(
            method=request.method, path=request.url.path, status=str(response.status_code)
        ).inc()
        metrics.HTTP_LATENCY.labels(
            method=request.method, path=request.url.path
        ).observe(time.perf_counter() - start)
        return response
