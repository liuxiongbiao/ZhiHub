"""契约测试：014 Widget 公共通道（P0-1）。"""

import pytest

from app.models.order import Order
from app.services import tenant_service
from app.services.widget_service import is_domain_allowed


def _mk_tenant_with_key(db, *, domains="example.com"):
    from app.models.tenant import Tenant
    tenant = Tenant(name="widget-tenant", plan_type="basic", status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    key = tenant_service.generate_widget_api_key(db, tenant.id)["api_key"]
    tenant_service.set_widget_allowed_domains(db, tenant.id, domains.split(","))
    return tenant, key


class TestDomainWhitelist:
    def test_exact_match(self):
        class T:
            widget_allowed_domains = "example.com"
        assert is_domain_allowed(T(), "https://example.com/x")

    def test_subdomain_match(self):
        class T:
            widget_allowed_domains = "example.com"
        assert is_domain_allowed(T(), "https://shop.example.com/x")

    def test_unrelated_rejected(self):
        class T:
            widget_allowed_domains = "example.com"
        assert not is_domain_allowed(T(), "https://evil.org/x")

    def test_empty_whitelist_blocks_in_production(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "app_env", "production")

        class T:
            widget_allowed_domains = ""
        assert not is_domain_allowed(T(), "https://localhost:5173/x")


class TestWidgetSessionApi:
    def test_create_session_with_valid_key(self, client, db, monkeypatch):
        tenant, key = _mk_tenant_with_key(db)
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["session_id"] > 0
        assert body["token"].startswith("ey")

        from app.crud import session as session_crud
        s = session_crud.get_by_id(db, body["session_id"], tenant.id)
        assert s is not None
        assert s.end_user_id.startswith("eu-")

    def test_invalid_key_rejected(self, client, db):
        _mk_tenant_with_key(db)
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": "wrong-key-123", "origin": "https://shop.example.com"},
        )
        assert resp.status_code == 401

    def test_domain_not_allowed(self, client, db):
        tenant, key = _mk_tenant_with_key(db)
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://evil.org"},
        )
        assert resp.status_code == 403

    def test_widget_token_cannot_access_staff_api(self, client, db):
        tenant, key = _mk_tenant_with_key(db)
        sess = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
        ).json()
        resp = client.get(
            "/api/v1/session/list",
            headers={"Authorization": f"Bearer {sess['token']}"},
        )
        assert resp.status_code == 401


class TestWidgetMessage:
    def test_send_message_ok(self, client, db, monkeypatch):
        tenant, key = _mk_tenant_with_key(db)
        sess = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
        ).json()

        from app.services import agent_service
        calls = {}

        def fake_handle(db_, *, session_id, tenant_id, user_message):
            calls["session_id"] = session_id
            calls["tenant_id"] = tenant_id
            calls["content"] = user_message
            return {"message_id": 1, "role": "assistant", "content": "模拟回复", "sources": [], "metadata": {}}

        monkeypatch.setattr(agent_service, "handle_message", fake_handle)
        resp = client.post(
            f"/api/v1/widget/{sess['session_id']}/message",
            headers={"Authorization": f"Bearer {sess['token']}"},
            json={"session_id": sess["session_id"], "content": "你好"},
        )
        assert resp.status_code == 200, resp.text
        assert calls["session_id"] == sess["session_id"]
        assert calls["tenant_id"] == tenant.id
        assert calls["content"] == "你好"

    def test_session_token_mismatch_rejected(self, client, db):
        tenant, key = _mk_tenant_with_key(db)
        sess = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
        ).json()
        resp = client.post(
            "/api/v1/widget/99999/message",
            headers={"Authorization": f"Bearer {sess['token']}"},
            json={"session_id": 99999, "content": "你好"},
        )
        assert resp.status_code == 403


class TestWidgetRefundCancel:
    def _mk_pending_refund(self, db, *, phone="13800138000"):
        from app.services.order_service import request_refund
        order = Order(
            tenant_id=1, order_no="ORD-WIDGET-1", amount=200.0,
            paid_amount=200.0, status="paid", version=1, phone=phone,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        res = request_refund(
            db, tenant_id=1, order_no="ORD-WIDGET-1", amount=50.0,
            reason="x", client_request_id="cid-widget-1", phone=phone,
        )
        db.commit()
        return res

    def test_cancel_with_matching_phone(self, client, db, monkeypatch):
        tenant, key = _mk_tenant_with_key(db)
        # 订单与退款审批属于 tenant.id（测试内固定 tenant_id=1 需对齐）
        tenant_id = tenant.id
        order = Order(
            tenant_id=tenant_id, order_no="ORD-WIDGET-2", amount=200.0,
            paid_amount=200.0, status="paid", version=1, phone="13800138000",
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        from app.services.order_service import request_refund
        res = request_refund(
            db, tenant_id=tenant_id, order_no="ORD-WIDGET-2", amount=50.0,
            reason="x", client_request_id="cid-widget-2", phone="13800138000",
        )
        db.commit()

        sess = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
        ).json()
        resp = client.post(
            f"/api/v1/widget/refunds/{res['id']}/cancel",
            headers={"Authorization": f"Bearer {sess['token']}"},
            json={"session_id": sess["session_id"], "phone": "13800138000"},
        )
        assert resp.status_code == 200, resp.text

    def test_cancel_with_wrong_phone_rejected(self, client, db):
        tenant, key = _mk_tenant_with_key(db)
        tenant_id = tenant.id
        order = Order(
            tenant_id=tenant_id, order_no="ORD-WIDGET-3", amount=200.0,
            paid_amount=200.0, status="paid", version=1, phone="13800138000",
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        from app.services.order_service import request_refund
        res = request_refund(
            db, tenant_id=tenant_id, order_no="ORD-WIDGET-3", amount=50.0,
            reason="x", client_request_id="cid-widget-3", phone="13800138000",
        )
        db.commit()

        sess = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
        ).json()
        resp = client.post(
            f"/api/v1/widget/refunds/{res['id']}/cancel",
            headers={"Authorization": f"Bearer {sess['token']}"},
            json={"session_id": sess["session_id"], "phone": "13900139000"},
        )
        assert resp.status_code == 400
