"""app/core/metrics.py

Prometheus 指标注册表（企业标准监控探针，审查  补齐）。

指标:
  http_requests_total            HTTP 请求计数（method/path/status）
  http_request_duration_seconds  HTTP 请求耗时直方图（method/path）
  zhihub_active_sessions         当前活跃会话数（Gauge，demo 由探针刷新）
  zhihub_agent_messages_total    Agent 对话消息计数（role）
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

# HTTP 层指标（metrics_middleware 记录）
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)

# 业务指标（demo：进程内 Gauge/计数器；生产可改为由采集器查询 Redis/DB）
ACTIVE_SESSIONS = Gauge(
    "zhihub_active_sessions",
    "Current active sessions (waiting_human + human_handling + agent_handling)",
)
AGENT_MESSAGES = Counter(
    "zhihub_agent_messages_total",
    "Total agent conversation messages",
    ["role"],
)


def render_metrics() -> tuple[bytes, str]:
    """渲染 Prometheus 文本格式（供 /metrics 端点）。"""
    return generate_latest(), CONTENT_TYPE_LATEST
