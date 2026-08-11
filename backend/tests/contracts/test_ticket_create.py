"""
backend/tests/contracts/test_ticket_create.py

职责: 用户主动提交工单（closure-plan P1-4，PRD 3.8）。
契约:
  POST /ticket -> 201 + ticket_no(TK-...)
  缺标题 -> 422；未登录 -> 401；category 透传
"""


class TestTicketCreate:
    def test_create_ticket_success(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/ticket",
            json={"title": "订单一直未发货", "description": "已支付 3 天", "priority": "high", "category": "user"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["ticket_no"].startswith("TK-")
        assert body["status"] == "open"
        assert body["priority"] == "high"

    def test_create_ticket_missing_title_422(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/ticket",
            json={"description": "no title"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_create_ticket_requires_auth(self, client):
        resp = client.post("/api/v1/ticket", json={"title": "x"})
        assert resp.status_code == 401

    def test_created_ticket_visible_in_list(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        created = client.post(
            "/api/v1/ticket", json={"title": "列表可见"}, headers={"Authorization": f"Bearer {token}"},
        ).json()
        listed = client.get("/api/v1/ticket/list?page_size=50", headers={"Authorization": f"Bearer {token}"}).json()
        assert any(t["ticket_no"] == created["ticket_no"] for t in listed["items"])
