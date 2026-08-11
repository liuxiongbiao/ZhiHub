"""
backend/tests/contracts/test_payment.py

职责: 测试 PRD 3.10 支付宝沙箱支付的 mock 契约（key 占位，真实验证待用户填 key）
对应日程: 010 业务闭环 / PRD 3.10

测试内容（mock 契约，monkeypatch alipay_client）:
  - TestCreatePayment: 生成支付链接 + trade_no
  - TestCallbackVerifySuccess: 验签通过 -> 账单 paid + trade_no + paid_at
  - TestCallbackVerifyFail: 验签失败 -> 拒绝（账单仍 unpaid）
  - TestCallbackIdempotent: 同 trade_no 重复回调不重复入账
  - TestRefundViaAlipay: approve_refund approved -> 调 alipay refund -> 订单 refunded
  - TestMockModeDefault: 未填 key 时 client 自动走 mock 模式

设计决策:
  - 用 monkeypatch 替换 alipay_client 单例方法，隔离真实支付宝网关
  - mock 模式下 verify_callback 默认返回 True，但本契约显式控制返回值以验证签名校验分支
  - 回调走 /api/v1/billing/alipay/notify 公开端点（免鉴权），表单 POST
"""

import pytest

from app.models.billing_invoice import BillingInvoice
from app.models.order import Order
from app.models.refund_approval import RefundApproval


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════
def _make_invoice(db, *, tenant_id=1, period="2026-08", status="unpaid", base_fee=99.0, overage_fee=0.0):
    """创建一条账单供支付/回调测试。"""
    inv = BillingInvoice(
        tenant_id=tenant_id, period=period, base_fee=base_fee,
        overage_fee=overage_fee, status=status,
    )
    db.add(inv)
    db.flush()
    return inv


def _patch_alipay(monkeypatch, *, verify=True, refund_ok=True, pay_url="https://openapi.alipaydev.com/gateway.do?mock=1"):
    """monkeypatch alipay_client 单例方法，返回受控结果。"""
    from app.core import alipay_client as ac

    def fake_create(trade_no, amount, subject):
        return f"{pay_url}&trade_no={trade_no}&amount={amount}&subject={subject}"

    def fake_verify(form_data):
        return verify

    def fake_refund(trade_no, amount):
        return refund_ok

    monkeypatch.setattr(ac.AlipayClient, "create_payment", lambda self, trade_no, amount, subject: fake_create(trade_no, amount, subject))
    monkeypatch.setattr(ac.AlipayClient, "verify_callback", lambda self, form_data: fake_verify(form_data))
    monkeypatch.setattr(ac.AlipayClient, "refund", lambda self, trade_no, amount: fake_refund(trade_no, amount))


# ═══════════════════════════════════════════════════════════
# T1: 创建支付订单 -> 生成支付链接 + trade_no
# ═══════════════════════════════════════════════════════════
class TestCreatePayment:
    """create_payment 契约：生成 trade_no 与支付链接。"""

    def test_create_payment_returns_link_and_trade_no(self, db, monkeypatch):
        _patch_alipay(monkeypatch)
        from app.services.billing_service import create_payment

        inv = _make_invoice(db)
        db.commit()

        result = create_payment(db, invoice_id=inv.id, tenant_id=1)

        assert "pay_url" in result
        assert result["pay_url"].startswith("https://openapi.alipaydev.com")
        assert result["trade_no"]
        assert result["invoice_id"] == inv.id
        assert result["amount"] == 99.0

    def test_create_payment_wrong_tenant(self, db, monkeypatch):
        """跨租户不可为他人账单发起支付。"""
        _patch_alipay(monkeypatch)
        from app.services.billing_service import create_payment, PaymentError

        inv = _make_invoice(db, tenant_id=1)
        db.commit()

        with pytest.raises(PaymentError):
            create_payment(db, invoice_id=inv.id, tenant_id=999)

    def test_create_payment_already_paid(self, db, monkeypatch):
        """已支付账单不可重复发起支付。"""
        _patch_alipay(monkeypatch)
        from app.services.billing_service import create_payment, PaymentError

        inv = _make_invoice(db, status="paid")
        db.commit()

        with pytest.raises(PaymentError, match="已支付"):
            create_payment(db, invoice_id=inv.id, tenant_id=1)


# ═══════════════════════════════════════════════════════════
# T2: 异步回调验签通过 -> 账单 paid
# ═══════════════════════════════════════════════════════════
class TestCallbackVerifySuccess:
    """回调验签通过，账单置为 paid 并回填 trade_no/paid_at。"""

    def test_verify_success_marks_paid(self, db, monkeypatch):
        _patch_alipay(monkeypatch, verify=True)
        from app.services.billing_service import handle_payment_callback

        inv = _make_invoice(db)
        db.commit()

        form = {"trade_no": "ALIPAY-20260809-0001", "out_trade_no": f"ALI-{inv.id}-abcd1234", "trade_status": "TRADE_SUCCESS"}
        result = handle_payment_callback(db, form_data=form, tenant_id=1)

        assert result["status"] == "paid"
        assert result["invoice_id"] == inv.id

        db.refresh(inv)
        assert inv.status == "paid"
        assert inv.payment_trade_no == "ALIPAY-20260809-0001"
        assert inv.paid_at is not None

    def test_verify_success_clears_degradation(self, db, monkeypatch):
        """支付成功应清除租户降级标记（恢复服务）。"""
        from app.core.redis import get_redis
        from app.services.billing_service import handle_payment_callback, is_degraded

        _patch_alipay(monkeypatch, verify=True)
        r = get_redis()
        r.set("billing:degraded:1", "1", ex=86400)
        assert is_degraded(1) is True

        inv = _make_invoice(db)
        db.commit()

        form = {"trade_no": "ALIPAY-CLR-001", "out_trade_no": f"ALI-{inv.id}-abcd1234", "trade_status": "TRADE_SUCCESS"}
        handle_payment_callback(db, form_data=form, tenant_id=1)

        assert is_degraded(1) is False


# ═══════════════════════════════════════════════════════════
# T3: 验签失败 -> 拒绝
# ═══════════════════════════════════════════════════════════
class TestCallbackVerifyFail:
    """验签失败，账单保持 unpaid。"""

    def test_verify_fail_rejected(self, db, monkeypatch):
        _patch_alipay(monkeypatch, verify=False)
        from app.services.billing_service import handle_payment_callback, PaymentError

        inv = _make_invoice(db)
        db.commit()

        form = {"trade_no": "ALIPAY-BAD-SIG", "out_trade_no": f"ALI-{inv.id}-abcd1234", "trade_status": "TRADE_SUCCESS"}
        with pytest.raises(PaymentError, match="验签失败"):
            handle_payment_callback(db, form_data=form, tenant_id=1)

        db.refresh(inv)
        assert inv.status == "unpaid"
        assert inv.payment_trade_no is None


# ═══════════════════════════════════════════════════════════
# T4: 回调幂等（trade_no 去重）
# ═══════════════════════════════════════════════════════════
class TestCallbackIdempotent:
    """同一 trade_no 重复回调不重复入账。"""

    def test_duplicate_trade_no_idempotent(self, db, monkeypatch):
        _patch_alipay(monkeypatch, verify=True)
        from app.services.billing_service import handle_payment_callback

        inv = _make_invoice(db)
        db.commit()

        form = {"trade_no": "ALIPAY-DUP-001", "out_trade_no": f"ALI-{inv.id}-abcd1234", "trade_status": "TRADE_SUCCESS"}
        first = handle_payment_callback(db, form_data=form, tenant_id=1)
        assert first["cached"] is False

        # 第二次同 trade_no 回调 -> 命中幂等
        second = handle_payment_callback(db, form_data=form, tenant_id=1)
        assert second["cached"] is True
        assert second["invoice_id"] == inv.id


# ═══════════════════════════════════════════════════════════
# T5: 退款 approved -> 调支付宝退款 -> 订单 refunded
# ═══════════════════════════════════════════════════════════
class TestRefundViaAlipay:
    """approve_refund approved 时调 alipay_client.refund，成功则订单 refunded。"""

    def _make_refunding_order(self, db, amount=100.0):
        order = Order(
            tenant_id=1, order_no="ORD-PAY-001", amount=299.0,
            paid_amount=299.0, status="refunding",
        )
        db.add(order)
        db.flush()
        approval = RefundApproval(
            order_id=order.id, amount=amount, reason="test",
            client_request_id=f"cid-pay-{order.id}", status="pending",
        )
        db.add(approval)
        db.flush()
        return order, approval

    def test_approve_calls_alipay_refund_success(self, db, monkeypatch):
        called = {"refund": 0}

        from app.core import alipay_client as ac
        monkeypatch.setattr(ac.AlipayClient, "create_payment", lambda self, *a, **k: "mock")
        monkeypatch.setattr(ac.AlipayClient, "verify_callback", lambda self, *a, **k: True)

        def fake_refund(self, trade_no, amount):
            called["refund"] += 1
            return True
        monkeypatch.setattr(ac.AlipayClient, "refund", fake_refund)

        from app.services.order_service import approve_refund

        order, approval = self._make_refunding_order(db, amount=100.0)
        # 关联账单的 trade_no，供退款调用
        inv = _make_invoice(db, status="paid")
        inv.payment_trade_no = "ALIPAY-PAY-001"
        db.commit()

        result = approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=2, tenant_id=1, role="admin")

        assert result["status"] == "approved"
        assert called["refund"] == 1
        db.refresh(order)
        assert order.status == "refunded"

    def test_approve_refund_fail_keeps_refunding(self, db, monkeypatch):
        """支付宝退款失败 -> 订单不进入 refunded，抛错回滚审批。"""
        from app.core import alipay_client as ac
        monkeypatch.setattr(ac.AlipayClient, "create_payment", lambda self, *a, **k: "mock")
        monkeypatch.setattr(ac.AlipayClient, "verify_callback", lambda self, *a, **k: True)
        monkeypatch.setattr(ac.AlipayClient, "refund", lambda self, trade_no, amount: False)

        from app.services.order_service import approve_refund, RefundError

        order, approval = self._make_refunding_order(db, amount=100.0)
        db.commit()

        with pytest.raises(RefundError):
            approve_refund(db, approval_id=approval.id, decision="approved",
                           approver_id=2, tenant_id=1, role="admin")

        db.refresh(order)
        assert order.status == "refunding"


# ═══════════════════════════════════════════════════════════
# T6: 默认 mock 模式（key 为空）
# ═══════════════════════════════════════════════════════════
class TestMockModeDefault:
    """未配置支付宝 key 时 client 走 mock 模式（不调真实网关）。"""

    def test_mock_mode_when_app_id_empty(self, monkeypatch):
        from app.core.alipay_client import get_alipay_client, reset_alipay_client
        from app.config import settings

        monkeypatch.setattr(settings, "alipay_app_id", "")
        reset_alipay_client()
        client = get_alipay_client()
        assert client.is_mock is True

        url = client.create_payment("T-MOCK-001", 99.0, "测试订单")
        # mock 链接须含 mock 标记 + 商户订单号（原断言 `or url.startswith` 对任何 URL 恒真）
        assert "mock=1" in url and "trade_no=T-MOCK-001" in url, url

        assert client.verify_callback({"trade_no": "T-MOCK-001"}) is True
        assert client.refund("T-MOCK-001", 99.0) is True

    def test_real_mode_when_app_id_set(self, monkeypatch):
        """配置了 app_id 即视为意图启用真实模式（is_mock=False）。

        真实 key 由用户后填，这里只验证模式开关：app_id 非空 -> 非 mock。
        用合法 PEM 头占位避免 SDK 解析抛错，验证 is_mock 判定基于 app_id 是否为空。
        """
        from app.core.alipay_client import AlipayClient
        from app.config import settings

        monkeypatch.setattr(settings, "alipay_app_id", "2021000xxxxxxxxx")
        # is_mock 仅由 app_id 是否为空决定（真实 key 待用户填）
        client = AlipayClient()
        # app_id 非空 -> is_mock=False（即便 key 非法，模式判定仍以 app_id 为准，方法降级）
        assert client.is_mock is False


# ═══════════════════════════════════════════════════════════
# T7: HTTP 端点契约
# ═══════════════════════════════════════════════════════════
class TestPaymentEndpoints:
    """HTTP 端点契约：/pay 与 /alipay/notify。"""

    def test_pay_endpoint_admin(self, client, db, make_token, monkeypatch):
        _patch_alipay(monkeypatch)
        inv = _make_invoice(db)
        db.commit()

        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            f"/api/v1/billing/pay/{inv.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "pay_url" in body and body["pay_url"]
        assert body["trade_no"]

    def test_pay_endpoint_agent_forbidden(self, client, db, make_token, monkeypatch):
        _patch_alipay(monkeypatch)
        inv = _make_invoice(db)
        db.commit()

        token = make_token(user_id=2, tenant_id=1, role="agent")
        resp = client.post(
            f"/api/v1/billing/pay/{inv.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_alipay_notify_public_success(self, client, db, monkeypatch):
        """回调端点公开免鉴权，验签通过 -> 账单 paid。"""
        _patch_alipay(monkeypatch, verify=True)
        inv = _make_invoice(db)
        db.commit()

        resp = client.post(
            "/api/v1/billing/alipay/notify",
            data={"trade_no": "ALIPAY-HTTP-001", "out_trade_no": f"ALI-{inv.id}-abcd1234", "trade_status": "TRADE_SUCCESS"},
        )
        assert resp.status_code == 200
        # T34: 原三重 or 任何含 "success" 都过，改为精确断言（端点返回纯文本 "success"）
        assert resp.text.strip('"') == "success"

    def test_alipay_notify_verify_fail_returns_fail(self, client, db, monkeypatch):
        """验签失败返回 fail（支付宝约定失败响应非 200 也行，这里返回 400）。"""
        _patch_alipay(monkeypatch, verify=False)
        inv = _make_invoice(db)
        db.commit()

        resp = client.post(
            "/api/v1/billing/alipay/notify",
            data={"trade_no": "ALIPAY-BAD-HTTP", "out_trade_no": f"ALI-{inv.id}-abcd1234", "trade_status": "TRADE_SUCCESS"},
        )
        assert resp.status_code == 400
