"""
backend/tests/contracts/test_metrics.py

职责: Prometheus 监控探针契约。
契约:
  GET /metrics 只接受 X-Platform-Token
  租户 JWT 与空令牌返回 401
  正确的平台令牌返回 Prometheus 文本
"""

import pytest


class TestMetrics:
    def test_tenant_jwt_cannot_read_metrics(self, client, make_token):
        headers = {"Authorization": f"Bearer {make_token(role='admin')}"}
        resp = client.get("/metrics", headers=headers)
        assert resp.status_code == 401

    def test_metrics_endpoint_format(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "platform_ops_token", "metrics-ops-token")
        resp = client.get("/metrics", headers={"X-Platform-Token": "metrics-ops-token"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "# HELP http_requests_total" in resp.text
        assert "# TYPE http_requests_total counter" in resp.text

    def test_metrics_records_http_requests(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "platform_ops_token", "metrics-ops-token")
        client.get("/health")
        resp = client.get("/metrics", headers={"X-Platform-Token": "metrics-ops-token"})
        assert 'http_requests_total{method="GET",path="/health",status="200"}' in resp.text

    def test_empty_platform_token_is_rejected(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "platform_ops_token", "")
        resp = client.get("/metrics", headers={"X-Platform-Token": "anything"})
        assert resp.status_code == 401
