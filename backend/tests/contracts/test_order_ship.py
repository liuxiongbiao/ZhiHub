"""
backend/tests/contracts/test_order_ship.py

职责: 测试 PRD 3.6 订单发货端点 POST /order/{order_id}/ship（修复审查报告 6.1）。
对应 feature: 013-prd-coverage-gap-closure / US6 / T008

测试内容（HTTP 契约）:
  - paid 订单发货 -> 200，status=shipped，tracking_no 写入
  - shipped 订单再次发货 -> 400（非法状态跃迁）
  - 不存在订单 -> 404
  - 空 tracking_no -> 422
  - 跨租户发货 -> 404（租户隔离）
"""

import pytest

from app.models.order import Order


def _make_paid_order(db, *, tenant_id=1, order_no="ORD-SHIP-001", status="paid"):
    o = Order(
        tenant_id=tenant_id, order_no=order_no, amount=299.0,
        paid_amount=299.0, status=status,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


class TestOrderShip:
    def test_ship_paid_order_success(self, client, db, make_token):
        o = _make_paid_order(db)
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            f"/api/v1/order/{o.id}/ship",
            json={"tracking_no": "SF1234567890"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "shipped"
        assert body["tracking_no"] == "SF1234567890"
        assert body["order_no"] == "ORD-SHIP-001"

    def test_ship_shipped_order_400(self, client, db, make_token):
        o = _make_paid_order(db, status="shipped", order_no="ORD-SHIP-002")
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            f"/api/v1/order/{o.id}/ship",
            json={"tracking_no": "SF222"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400

    def test_ship_nonexistent_404(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/order/999999/ship",
            json={"tracking_no": "SF999"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    def test_ship_empty_tracking_no_422(self, client, db, make_token):
        o = _make_paid_order(db, order_no="ORD-SHIP-003")
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            f"/api/v1/order/{o.id}/ship",
            json={"tracking_no": ""},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_ship_cross_tenant_404(self, client, db, make_token):
        # tenant_id=2 的订单，用 tenant_id=1 的 token 访问 -> 404（租户隔离）
        o = _make_paid_order(db, tenant_id=2, order_no="ORD-SHIP-004")
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            f"/api/v1/order/{o.id}/ship",
            json={"tracking_no": "SF444"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


class TestOrderListTrackingNo:
    """订单列表/详情响应必须包含 tracking_no（前端物流单号列依赖，修复 013 UI 联调 bug）。"""

    def test_list_orders_includes_tracking_no(self, client, db, make_token):
        o = _make_paid_order(db, order_no="ORD-SHIP-LIST-01")
        from app.services.order_service import ship_order
        ship_order(db, order_id=o.id, tenant_id=1, tracking_no="SF-LIST-001")
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.get(
            "/api/v1/order/list",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        shipped = [i for i in items if i["order_no"] == "ORD-SHIP-LIST-01"]
        assert shipped and shipped[0]["tracking_no"] == "SF-LIST-001"

    def test_order_detail_includes_tracking_no(self, client, db, make_token):
        o = _make_paid_order(db, order_no="ORD-SHIP-DETAIL-01")
        from app.services.order_service import ship_order
        ship_order(db, order_id=o.id, tenant_id=1, tracking_no="SF-DETAIL-001")
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.get(
            f"/api/v1/order/{o.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["tracking_no"] == "SF-DETAIL-001"
