"""PRD §5：运营事件写入、租户聚合与平台双重鉴权契约。"""

from datetime import datetime
from decimal import Decimal

from app.config import settings
from app.models.billing_invoice import BillingInvoice
from app.models.tenant import Tenant
from app.services import analytics_service


class _FakeCollection:
    def __init__(self):
        self.indexes = []
        self.documents = []

    def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    def insert_one(self, document):
        self.documents.append(document)
        return object()


def test_prd_event_catalog_is_complete():
    assert {
        "session_created", "retrieval_executed", "mcp_tool_called", "human_takeover",
        "order_state_changed", "refund_approved", "refund_rejected", "quota_exceeded",
        "invoice_paid", "security_block", "answer_feedback", "index_rebuild_triggered",
        "degradation_mode_entered", "degradation_mode_exited", "message_left",
    }.issubset(analytics_service.ALLOWED_EVENTS)


def _tenant(db, name: str, plan: str) -> Tenant:
    item = Tenant(name=name, plan_type=plan, status="active")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_event_write_masks_pii_and_builds_indexes(monkeypatch):
    collection = _FakeCollection()
    monkeypatch.setattr(analytics_service, "_collection", collection)
    monkeypatch.setattr(analytics_service, "_indexes_ready", False)

    ok = analytics_service.track_event(
        "retrieval_executed",
        tenant_id=9,
        properties={"query": "请查 13800138000 的退款", "latency": 12.3456789},
    )

    assert ok is True
    assert len(collection.indexes) == 2
    assert collection.documents[0]["tenant_id"] == 9
    assert "13800138000" not in collection.documents[0]["properties"]["query"]
    assert collection.documents[0]["properties"]["latency"] == 12.345679


def test_event_write_is_fail_open(monkeypatch):
    class BrokenCollection:
        def create_index(self, *_args, **_kwargs):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(analytics_service, "_collection", BrokenCollection())
    monkeypatch.setattr(analytics_service, "_indexes_ready", False)
    assert analytics_service.track_event("session_created", tenant_id=1) is False
    assert analytics_service.track_event("unknown", tenant_id=1) is False


def test_tenant_overview_isolated_and_aggregated(client, db, make_token, monkeypatch):
    tenant = _tenant(db, "租户甲", "pro")
    other = _tenant(db, "租户乙", "enterprise")
    now = datetime.utcnow()
    db.add_all(
        [
            BillingInvoice(
                tenant_id=tenant.id,
                period=now.strftime("%Y-%m"),
                base_fee=Decimal("899.00"),
                overage_fee=Decimal("10.00"),
                status="paid",
                paid_at=now,
                payment_trade_no="TRADE-TENANT",
            ),
            BillingInvoice(
                tenant_id=other.id,
                period=now.strftime("%Y-%m"),
                base_fee=Decimal("9999.00"),
                overage_fee=Decimal("0"),
                status="paid",
                paid_at=now,
                payment_trade_no="TRADE-OTHER",
            ),
        ]
    )
    db.commit()

    tenant_events = [
        {
            "event_name": "retrieval_executed",
            "tenant_id": tenant.id,
            "occurred_at": now,
            "properties": {
                "vector_top_score": 0.51,
                "hybrid_top_score": 0.82,
                "latency": 42,
                "hyde_fallback": False,
            },
        },
        {
            "event_name": "session_resolved",
            "tenant_id": tenant.id,
            "occurred_at": now,
            "properties": {"resolution_mode": "auto"},
        },
        {
            "event_name": "mcp_tool_called",
            "tenant_id": tenant.id,
            "occurred_at": now,
            "properties": {"success": True},
        },
    ]
    monkeypatch.setattr(
        analytics_service,
        "_load_events",
        lambda tenant_id, _since: (tenant_events if tenant_id in (None, tenant.id) else [], True),
    )

    token = make_token(user_id=1, tenant_id=tenant.id, role="admin")
    response = client.get(
        "/api/v1/analytics/overview?days=30",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "tenant"
    assert body["summary"]["auto_resolution_rate"] == 100
    assert body["summary"]["mcp_success_rate"] == 100
    assert body["retrieval_comparison"][1]["avg_score"] == 0.82
    assert body["plan_distribution"] == [{"plan": "pro", "count": 1}]
    assert body["revenue_trend"][0]["revenue"] == 909

    agent_token = make_token(user_id=2, tenant_id=tenant.id, role="agent")
    denied = client.get(
        "/api/v1/analytics/overview",
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert denied.status_code == 403

    assert client.get(
        "/api/v1/analytics/platform/overview",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 403

    monkeypatch.setattr(settings, "platform_ops_token", "ops-secret")
    platform = client.get(
        "/api/v1/analytics/platform/overview",
        headers={"Authorization": f"Bearer {token}", "X-Platform-Token": "ops-secret"},
    )
    assert platform.status_code == 200
    assert platform.json()["scope"] == "platform"
    assert {item["plan"] for item in platform.json()["plan_distribution"]} == {"pro", "enterprise"}
