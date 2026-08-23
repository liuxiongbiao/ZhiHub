"""第 5 阶段加固行为的契约测试（///////）。"""

import json
import time
from datetime import date

import pytest

from app.core import circuit_breaker as cb
from app.core.security import decode_access_token
from app.models.order import Order
from app.models.refund_outbox import RefundOutbox
from app.models.tenant import Tenant
from app.models.usage_record import UsageRecord


# ────────── 租户状态门禁 ──────────
class TestTenantStatusGate:
    def test_readonly_blocks_new_session(self, db):
        from app.services import session_service
        from app.services.tenant_service import TenantInactive

        t = Tenant(name="readonly租户", plan_type="trial", seat_limit=1, status="readonly")
        db.add(t)
        db.commit()
        with pytest.raises(TenantInactive):
            session_service.create_session(db, tenant_id=t.id)

    def test_pending_review_blocks_widget_session(self, db):
        from app.services.widget_service import _hash_api_key
        from app.services.widget_service import TenantUnavailable, create_widget_session

        t = Tenant(name="待审租户", plan_type="enterprise", seat_limit=10, status="pending_review")
        t.widget_api_key_hash = _hash_api_key("test-key-123")
        t.widget_allowed_domains = "example.com"
        db.add(t)
        db.commit()
        with pytest.raises(TenantUnavailable):
            create_widget_session(db, api_key="test-key-123", origin="https://example.com")

    def test_closed_blocks_message_on_existing_session(self, db, fake_redis):
        from app.crud import session as session_crud
        from app.services import agent_service
        from app.services.tenant_service import TenantInactive

        t = Tenant(name="注销租户", plan_type="trial", seat_limit=1, status="closed")
        db.add(t)
        db.commit()
        s = session_crud.create_session(db, tenant_id=t.id)
        db.commit()
        with pytest.raises(TenantInactive):
            agent_service.handle_message(db, session_id=s.id, tenant_id=t.id, user_message="hi")


# ────────── 退款流水与状态回退 ──────────
class TestRefundFailClosed:
    def test_approve_without_trade_no_fails_and_marks_failed_outbox(self, db):
        from app.services.order_service import RefundError, approve_refund, request_refund

        order = Order(tenant_id=1, order_no="ORD-NOTRADE-1", amount=100.0, paid_amount=100.0, status="paid")
        db.add(order)
        db.commit()
        req = request_refund(
            db, tenant_id=1, order_no="ORD-NOTRADE-1", amount=10.0,
            reason=None, client_request_id="cid-notrade-1",
        )
        with pytest.raises(RefundError):
            approve_refund(db, approval_id=req["id"], decision="approved", approver_id=2, tenant_id=1, role="admin")
        outbox = db.query(RefundOutbox).filter(RefundOutbox.approval_id == req["id"]).first()
        assert outbox is not None
        assert outbox.status == "failed"

    def test_reject_restores_shipped(self, db):
        from app.services.order_service import approve_refund, request_refund

        order = Order(
            tenant_id=1, order_no="ORD-SHIP-1", amount=100.0, paid_amount=100.0,
            status="shipped", payment_trade_no="ALIPAY-SHIP-1",
        )
        db.add(order)
        db.commit()
        req = request_refund(
            db, tenant_id=1, order_no="ORD-SHIP-1", amount=10.0,
            reason=None, client_request_id="cid-ship-1",
        )
        approve_refund(db, approval_id=req["id"], decision="rejected", approver_id=2, tenant_id=1, role="admin")
        db.refresh(order)
        assert order.status == "shipped"

    def test_tenant_threshold_overrides_global(self, db):
        from app.services.order_service import approve_refund, request_refund

        t = Tenant(name="阈值租户", plan_type="trial", seat_limit=1, status="active")
        t.refund_approval_threshold = 500
        db.add(t)
        db.commit()
        order = Order(
            tenant_id=t.id, order_no="ORD-THR-1", amount=1000.0, paid_amount=1000.0,
            status="paid", payment_trade_no="ALIPAY-THR-1",
        )
        db.add(order)
        db.commit()
        req = request_refund(
            db, tenant_id=t.id, order_no="ORD-THR-1", amount=800.0,
            reason=None, client_request_id="cid-thr-1",
        )
        # 非 admin 批准 800 > 租户阈值 500 → 保持 pending
        result = approve_refund(db, approval_id=req["id"], decision="approved", approver_id=3, tenant_id=t.id, role="agent")
        assert result["status"] == "pending"


# ────────── 账单读取 usage_records ──────────
class TestInvoiceFromUsageRecords:
    def test_invoice_sums_usage_records(self, db):
        from app.services.billing_service import generate_invoice

        t = Tenant(name="用量租户", plan_type="trial", seat_limit=1, status="active")
        db.add(t)
        db.commit()
        db.add(UsageRecord(tenant_id=t.id, date=date(2026, 8, 1), conversation_count=120, is_overage=True))
        db.add(UsageRecord(tenant_id=t.id, date=date(2026, 8, 2), conversation_count=30, is_overage=True))
        db.commit()
        inv = generate_invoice(db, tenant_id=t.id, period="2026-08")
        # trial quota=100；总用量 150 → 超额 50 * 0.5 = 25
        assert inv["base_fee"] == 0
        assert inv["overage_fee"] == 25.0


# ────────── 流式跳过生成 ──────────
class TestStreamSkipGeneration:
    def test_knowledge_node_skips_llm_when_flag_set(self, db, fake_redis, monkeypatch):
        from app.agents.nodes import knowledge_agent as ka
        from app.services import retrieval_service
        from types import SimpleNamespace

        class FakeResult:
            low_confidence = False
            hybrid_results = [SimpleNamespace(doc_filename="a.pdf", chunk_text="x", chunk_index=0, score=0.9)]

        monkeypatch.setattr(retrieval_service, "hybrid_search", lambda *a, **k: FakeResult())
        called = {"llm": 0}

        def fake_generate(*a, **k):
            called["llm"] += 1
            return "answer"

        monkeypatch.setattr(ka, "_generate_answer", fake_generate)

        result = ka.knowledge_agent_node({
            "messages": [{"role": "user", "content": "退货政策是什么"}],
            "tenant_id": 1,
            "skip_generation": True,
        })
        assert result["final_response"] == ""
        assert called["llm"] == 0


# ────────── WS 短效令牌 ──────────
class TestWsToken:
    def test_ws_token_endpoint_issues_short_lived_token(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="agent")
        resp = client.post(
            "/api/v1/auth/ws-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        payload = decode_access_token(resp.json()["token"])
        assert payload is not None
        assert payload.get("ws") is True
        assert payload.get("role") == "agent"
        # 有效期约 1 分钟
        assert payload["exp"] - payload["iat"] <= 120

    def test_ws_token_rejected_for_end_user(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="end_user")
        resp = client.post(
            "/api/v1/auth/ws-token",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ────────── 熔断器单探测 ──────────
class TestCircuitBreakerSingleProbe:
    def test_half_open_allows_only_one_probe(self, fake_redis):
        tool = "query_order"
        state = cb._default_state()
        state["state"] = cb.STATE_OPEN
        state["opened_at"] = time.time() - cb.HALF_OPEN_TIMEOUT - 10
        from app.core.redis import get_redis
        get_redis().set(cb._key(tool), json.dumps(state), ex=cb.CIRCUIT_TTL)

        assert cb.is_broken(tool) is False  # 首个探测放行
        assert cb.is_broken(tool) is True   # 探测权被占用，其余短路
        cb.record_failure(tool)
        assert cb.get_state(tool)["state"] == cb.STATE_OPEN


# ────────── 生产环境 mock 门禁 ──────────
class TestProductionMockGuards:
    def test_alipay_guard_blocks_mock_in_production(self, monkeypatch):
        from app.config import settings
        from app.core import alipay_client as ac

        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "alipay_app_id", "")
        ac.reset_alipay_client()
        with pytest.raises(RuntimeError):
            ac.get_alipay_client()
        ac.reset_alipay_client()

    def test_wechat_guard_blocks_mock_in_production(self, monkeypatch):
        from app.config import settings
        from app.core.wechat_client import WeChatClient

        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "wechat_app_id", "")
        with pytest.raises(RuntimeError):
            WeChatClient()
