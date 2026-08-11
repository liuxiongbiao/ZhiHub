"""
backend/tests/contracts/test_billing_export.py

职责: 用量明细 CSV 导出端点 GET /billing/usage/export（PRD 3.9，审查 P1-5）。
契约: 仅 admin；返回 text/csv；含表头与会话/Token 明细。
"""

from app.models.usage_record import UsageRecord


class TestUsageExport:
    def test_export_csv_admin(self, client, db, make_token):
        from datetime import date
        db.add(UsageRecord(
            tenant_id=1, date=date(2026, 8, 1), conversation_count=3, is_overage=False,
        ))
        db.commit()
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.get(
            "/api/v1/billing/usage/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "conversation_count" in resp.text
        assert "is_overage" in resp.text

    def test_export_forbidden_for_agent(self, client, make_token):
        token = make_token(user_id=2, tenant_id=1, role="agent")
        resp = client.get(
            "/api/v1/billing/usage/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
