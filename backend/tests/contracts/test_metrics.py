"""
backend/tests/contracts/test_metrics.py

职责: Prometheus 监控探针（审查 P2 补齐）契约。
契约:
  GET /metrics -> 200 + text/plain; version=0.0.4（Prometheus 文本格式）
  包含 http_requests_total 指标；访问 /health 后该指标计数出现 200 标签
"""


class TestMetrics:
    def test_metrics_endpoint_format(self, client, make_token):
        headers = {"Authorization": f"Bearer {make_token(role='admin')}"}
        resp = client.get("/metrics", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "# HELP http_requests_total" in resp.text
        assert "# TYPE http_requests_total counter" in resp.text

    def test_metrics_records_http_requests(self, client, make_token):
        headers = {"Authorization": f"Bearer {make_token(role='admin')}"}
        # 先访问 health 触发指标记录，再抓取 /metrics 应能看到 200 计数
        client.get("/health")
        resp = client.get("/metrics", headers=headers)
        assert 'http_requests_total{method="GET",path="/health",status="200"}' in resp.text
