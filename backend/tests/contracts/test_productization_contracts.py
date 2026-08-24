"""产品化增强契约：安全演示登录、业务上下文、看板计数与可执行工作流。"""

from decimal import Decimal

from app.config import settings
from app.core.rabbitmq import KNOWLEDGE_QUEUE_BUCKETS, knowledge_queue_for_tenant
from app.core.security import hash_password
from app.crud import session as session_crud
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud
from app.models.order import Order
from app.models.tenant import Tenant
from app.models.ticket import Ticket
from app.schemas.knowledge import SearchTestResponse


def _tenant(db, name: str = "产品化测试租户", *, is_demo: bool = False) -> Tenant:
    item = Tenant(
        name=name,
        status="active",
        plan_type="pro",
        seat_limit=10,
        is_demo=is_demo,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _authorization(make_token, tenant_id: int, role: str = "admin") -> dict[str, str]:
    token = make_token(user_id=101, tenant_id=tenant_id, role=role)
    return {"Authorization": f"Bearer {token}"}


def test_ticket_board_returns_global_counts_and_advances_state(client, db, make_token):
    tenant = _tenant(db)
    db.add_all([
        Ticket(tenant_id=tenant.id, ticket_no="TK-PROD-001", title="待处理问题", status="open", priority="high"),
        Ticket(tenant_id=tenant.id, ticket_no="TK-PROD-002", title="处理中问题", status="in_progress", priority="medium"),
        Ticket(tenant_id=tenant.id, ticket_no="TK-PROD-003", title="已关闭问题", status="closed", priority="low"),
    ])
    db.commit()
    headers = _authorization(make_token, tenant.id)

    listing = client.get("/api/v1/ticket/list?status=open", headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert [item["ticket_no"] for item in body["items"]] == ["TK-PROD-001"]
    assert body["status_counts"] == {"open": 1, "in_progress": 1, "closed": 1}

    ticket_id = body["items"][0]["id"]
    advanced = client.post(
        f"/api/v1/ticket/{ticket_id}/transition",
        json={"status": "in_progress"},
        headers=headers,
    )
    assert advanced.status_code == 200
    assert advanced.json()["status"] == "in_progress"

    illegal = client.post(
        f"/api/v1/ticket/{ticket_id}/transition",
        json={"status": "closed"},
        headers=headers,
    )
    assert illegal.status_code == 409


def test_ticket_transition_requires_staff_role(client, db, make_token):
    tenant = _tenant(db)
    ticket = Ticket(tenant_id=tenant.id, ticket_no="TK-PROD-ROLE", title="权限测试")
    db.add(ticket)
    db.commit()
    response = client.post(
        f"/api/v1/ticket/{ticket.id}/transition",
        json={"status": "in_progress"},
        headers=_authorization(make_token, tenant.id, role="user"),
    )
    assert response.status_code == 403


def test_order_board_filters_server_side_and_keeps_global_counts(client, db, make_token):
    tenant = _tenant(db)
    other = _tenant(db, "其他租户")
    db.add_all([
        Order(tenant_id=tenant.id, order_no="ORD-PROD-001", amount=Decimal("99"), paid_amount=Decimal("99"), status="paid"),
        Order(tenant_id=tenant.id, order_no="ORD-PROD-002", amount=Decimal("199"), paid_amount=Decimal("199"), status="shipped"),
        Order(tenant_id=other.id, order_no="ORD-OTHER-001", amount=Decimal("299"), paid_amount=Decimal("299"), status="paid"),
    ])
    db.commit()

    response = client.get(
        "/api/v1/order/list?status=paid&page_size=100",
        headers=_authorization(make_token, tenant.id),
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["order_no"] for item in body["items"]] == ["ORD-PROD-001"]
    assert body["total"] == 1
    assert body["status_counts"] == {"paid": 1, "shipped": 1}
    assert "ORD-OTHER-001" not in str(body)


def test_business_context_only_uses_explicit_same_tenant_records(client, db, make_token):
    tenant = _tenant(db)
    other = _tenant(db, "隔离租户")
    order = Order(
        tenant_id=tenant.id,
        order_no="ORD-CONTEXT-001",
        amount=Decimal("899"),
        paid_amount=Decimal("899"),
        status="shipped",
        tracking_no="SF-CONTEXT-001",
    )
    other_order = Order(
        tenant_id=other.id,
        order_no="ORD-CONTEXT-OTHER",
        amount=Decimal("999"),
        paid_amount=Decimal("999"),
        status="paid",
    )
    db.add_all([order, other_order])
    db.flush()
    session = session_crud.create_session(db, tenant_id=tenant.id, end_user_id="visitor-1")
    session_crud.create_message(
        db,
        session_id=session.id,
        role="user",
        content="请查 ORD-CONTEXT-001，绑定手机号 13911112222，看看物流到哪了",
    )
    db.commit()

    response = client.get(
        f"/api/v1/session/{session.id}/business-context",
        headers=_authorization(make_token, tenant.id, role="agent"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["order"]["order_no"] == "ORD-CONTEXT-001"
    assert body["order"]["tracking_no"] == "SF-CONTEXT-001"
    assert body["detected"]["phone_last4"] == "2222"
    assert "13911112222" not in str(body)
    assert "ORD-CONTEXT-OTHER" not in str(body)

    denied = client.get(
        f"/api/v1/session/{session.id}/business-context",
        headers=_authorization(make_token, other.id, role="agent"),
    )
    assert denied.status_code == 404


def test_demo_login_is_capability_gated_and_never_accepts_credentials(
    client, db, monkeypatch,
):
    tenant = _tenant(db, "安全演示租户", is_demo=True)
    user = user_crud.create(
        db,
        phone="13900000000",
        password_hash=hash_password("LocalOnly9x"),
        role_default="admin",
    )
    tenant_crud.add_member(db, tenant_id=tenant.id, user_id=user.id, role="admin")
    db.commit()
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "demo_quick_login_enabled", True)

    options = client.get("/api/v1/auth/login/options")
    assert options.status_code == 200
    assert options.json()["demo_quick_login"] is True
    assert options.json()["demo_personas"] == ["admin", "agent1", "agent2"]
    assert "13900000000" not in options.text
    assert "LocalOnly9x" not in options.text

    login = client.post("/api/v1/auth/login/demo", json={"persona": "admin"})
    assert login.status_code == 200
    assert login.json()["role"] == "admin"
    assert login.json()["tenant_id"] == tenant.id

    malformed = client.post(
        "/api/v1/auth/login/demo",
        json={"persona": "admin", "password": "must-not-be-used"},
    )
    assert malformed.status_code == 422

    tenant.is_demo = False
    db.commit()
    assert client.post(
        "/api/v1/auth/login/demo", json={"persona": "admin"},
    ).status_code == 404


def test_knowledge_queue_sharding_is_stable_and_bounded():
    first = [knowledge_queue_for_tenant(tenant_id) for tenant_id in range(64)]
    second = [knowledge_queue_for_tenant(tenant_id) for tenant_id in range(64)]
    assert first == second
    assert len(set(first)) == KNOWLEDGE_QUEUE_BUCKETS
    assert all(name.startswith("knowledge.") for name in first)


def test_search_response_keeps_stage_timings():
    response = SearchTestResponse(
        query="退款政策",
        rewritten_query=None,
        vector_results=[],
        hybrid_results=[],
        hyde_fallback=False,
        empty_knowledge=False,
        elapsed_ms=31,
        timings_ms={"parallel_recall": 12, "rerank": 16},
    )
    assert response.model_dump()["timings_ms"] == {
        "parallel_recall": 12,
        "rerank": 16,
    }
