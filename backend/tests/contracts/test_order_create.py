"""
backend/tests/contracts/test_order_create.py

职责: 订单业务创建入口（closure-plan P1-3，PRD 3.6）。
契约:
  POST /order -> 201 + status=created
  重复 order_no -> 409；非法金额 -> 422；租户隔离校验
"""


class TestOrderCreate:
    def test_create_order_success(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/order",
            json={"order_no": "ORD-NEW-0001", "amount": 299.0, "paid_amount": 299.0, "phone": "13800000001"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["order_no"] == "ORD-NEW-0001"
        assert body["status"] == "created"

    def test_create_order_duplicate_409(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        payload = {"order_no": "ORD-NEW-0002", "amount": 99.0, "paid_amount": 99.0}
        r1 = client.post("/api/v1/order", json=payload, headers={"Authorization": f"Bearer {token}"})
        assert r1.status_code == 201
        r2 = client.post("/api/v1/order", json=payload, headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 409

    def test_create_order_invalid_amount_422(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/order",
            json={"order_no": "ORD-NEW-0003", "amount": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_create_order_agent_allowed(self, client, make_token):
        token = make_token(user_id=2, tenant_id=1, role="agent")
        resp = client.post(
            "/api/v1/order",
            json={"order_no": "ORD-NEW-0004", "amount": 199.0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201

    def test_create_order_requires_auth(self, client):
        resp = client.post("/api/v1/order", json={"order_no": "ORD-NEW-0005", "amount": 1.0})
        assert resp.status_code == 401
