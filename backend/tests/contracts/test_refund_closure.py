"""契约测试：规格 016 退款闭环。"""

import pytest

from app.models.order import Order, VALID_TRANSITIONS
from app.services.order_service import OrderError, request_refund


def _mk_order(db, *, status="paid", phone="13800138000", paid_amount=200.0, order_no="ORD-REFUND-1"):
    o = Order(
        tenant_id=1, order_no=order_no, amount=paid_amount,
        paid_amount=paid_amount, status=status, version=1, phone=phone,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


class TestShippedRefund:
    def test_shipped_to_refund_requested_allowed(self):
        assert "refund_requested" in VALID_TRANSITIONS["shipped"]

    def test_request_refund_from_shipped(self, db, fake_redis):
        _mk_order(db, status="shipped", order_no="ORD-SHIPPED-1")
        res = request_refund(
            db, tenant_id=1, order_no="ORD-SHIPPED-1", amount=100.0,
            reason="已发货退款", client_request_id="cid-shipped-1", phone="13800138000",
        )
        assert res["status"] == "pending"
        assert db.get(Order, res["order_id"]).status == "refund_requested"


class TestRefundIdentity:
    def test_phone_suffix_mismatch_rejected(self, db, fake_redis):
        _mk_order(db, status="paid", phone="13800138000", order_no="ORD-ID-1")
        with pytest.raises(OrderError, match="身份校验"):
            request_refund(
                db, tenant_id=1, order_no="ORD-ID-1", amount=50.0,
                reason="x", client_request_id="cid-id-1", phone="13900139000",
            )

    def test_phone_required_on_mcp_path(self):
        from app.services.mcp_service import validate_params, InvalidParams
        with pytest.raises(InvalidParams, match="phone"):
            validate_params("request_refund", {"order_no": "ORD-1", "amount": 10.0, "client_request_id": "c1"})


class TestRefundApi:
    def test_agent_can_create_refund_request(self, client, db, make_token):
        _mk_order(db, status="paid", phone="13800138000", order_no="ORD-API-1")
        headers = {"Authorization": f"Bearer {make_token(role='agent')}"}
        resp = client.post(
            "/api/v1/order/refunds/request",
            headers=headers,
            json={
                "order_no": "ORD-API-1", "phone": "13800138000",
                "amount": 50.0, "reason": "测试", "client_request_id": "cid-api-1",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["client_request_id"] == "cid-api-1"

    def test_agent_mismatch_phone_rejected(self, client, db, make_token):
        _mk_order(db, status="paid", phone="13800138000", order_no="ORD-API-2")
        headers = {"Authorization": f"Bearer {make_token(role='agent')}"}
        resp = client.post(
            "/api/v1/order/refunds/request",
            headers=headers,
            json={
                "order_no": "ORD-API-2", "phone": "13900139000",
                "amount": 50.0, "reason": "x", "client_request_id": "cid-api-2",
            },
        )
        assert resp.status_code == 400

    def test_duplicate_request_id_idempotent(self, client, db, make_token):
        _mk_order(db, status="paid", phone="13800138000", order_no="ORD-API-3")
        headers = {"Authorization": f"Bearer {make_token(role='agent')}"}
        body = {
            "order_no": "ORD-API-3", "phone": "13800138000",
            "amount": 50.0, "reason": "x", "client_request_id": "cid-api-3",
        }
        r1 = client.post("/api/v1/order/refunds/request", headers=headers, json=body)
        r2 = client.post("/api/v1/order/refunds/request", headers=headers, json=body)
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"]
