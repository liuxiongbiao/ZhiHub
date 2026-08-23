"""契约测试： 工单创建端点幂等（client_request_id）。

工单 POST 必须支持可靠幂等键：同租户同 client_request_id 重复提交返回同一工单，
总数只增加一次。E2E 据此对工单 POST 做带幂等键的安全重试。
"""

from app.crud import tenant as tenant_crud


class TestTicketCreateIdempotency:
    def test_same_client_request_id_returns_same_ticket(self, client, make_token, db):
        tenant_crud.create_tenant(db, name="工单幂等测试租户", plan_type="basic")
        db.commit()
        token = make_token(user_id=1, tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "title": " 幂等工单",
            "description": "重复提交验证",
            "priority": "medium",
            "category": "general",
            "client_request_id": "p032-ticket-cid-0001",
        }

        r1 = client.post("/api/v1/ticket", json=body, headers=headers)
        assert r1.status_code == 201, r1.text
        r2 = client.post("/api/v1/ticket", json=body, headers=headers)
        assert r2.status_code == 201, r2.text

        id1, id2 = r1.json()["id"], r2.json()["id"]
        assert id1 == id2  # 同一审批/工单
        assert r2.json().get("cached") is True  # 第二次是缓存命中

        listing = client.get("/api/v1/ticket/list", headers=headers)
        matched = [t for t in listing.json()["items"] if t["title"] == " 幂等工单"]
        assert len(matched) == 1  # 总数只增加一次

    def test_different_client_request_id_creates_separate_tickets(self, client, make_token, db):
        tenant_crud.create_tenant(db, name="工单幂等测试租户2", plan_type="basic")
        db.commit()
        token = make_token(user_id=1, tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}
        base = {
            "title": " 不同幂等键",
            "priority": "low",
            "category": "general",
        }

        r1 = client.post("/api/v1/ticket", json={**base, "client_request_id": "cid-a"}, headers=headers)
        r2 = client.post("/api/v1/ticket", json={**base, "client_request_id": "cid-b"}, headers=headers)
        assert r1.json()["id"] != r2.json()["id"]

    def test_without_client_request_id_still_works(self, client, make_token, db):
        """幂等键可选：不传时保持旧行为（每次新建）。"""
        tenant_crud.create_tenant(db, name="工单幂等测试租户3", plan_type="basic")
        db.commit()
        token = make_token(user_id=1, tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post("/api/v1/ticket", json={"title": "无幂等键工单"}, headers=headers)
        assert r.status_code == 201
