"""
POST /api/v1/session/message 路由回归测试。

7c6496b 把路由装饰器留在了辅助函数 _reject_busy_user 上，send_message 未被注册，
接口不再调用 Agent 并返回 500。这里锁定路由绑定、请求体契约和完整的 Agent 调用链路。
"""

from sqlalchemy import select

from app.models.message import Message
from app.models.session import Session as SessionModel
from app.models.tenant import Tenant


def _message_route():
    from app.main import app

    def walk(routes, prefix=""):
        for route in routes:
            if hasattr(route, "path") and hasattr(route, "endpoint"):
                yield prefix + route.path, route
                continue
            context = getattr(route, "include_context", None)
            yield from walk(route.original_router.routes, prefix + (getattr(context, "prefix", "") or ""))

    matches = [
        route for path, route in walk(app.routes)
        if path == "/api/v1/session/message" and "POST" in getattr(route, "methods", set())
    ]
    assert len(matches) == 1
    return matches[0]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _FakeGraph:
    def __init__(self):
        self.states: list[dict] = []

    def invoke(self, state: dict) -> dict:
        self.states.append({**state, "messages": list(state["messages"])})
        return {
            **state,
            "final_response": "订单发货后 7 天内可申请退款。",
            "intent": "knowledge_consult",
            "confidence": 0.91,
            "sentiment": "normal",
            "should_interrupt": False,
            "retrieval_result": [
                {"doc_filename": "售后政策.md", "chunk_index": 2, "chunk_text": "发货后 7 天内可申请退款"},
            ],
        }


class TestSessionMessageRoute:
    def test_route_is_bound_to_send_message(self):
        assert _message_route().endpoint.__name__ == "send_message"

    def test_openapi_keeps_send_message_request_body(self):
        from app.main import app

        operation = app.openapi()["paths"]["/api/v1/session/message"]["post"]
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/SendMessageRequest")
        assert "422" in operation["responses"]

    def test_missing_content_is_rejected_by_request_validation(self, client, make_token):
        response = client.post(
            "/api/v1/session/message",
            json={"session_id": 1},
            headers=_auth(make_token(user_id=7101, tenant_id=1, role="agent")),
        )
        assert response.status_code == 422

    def test_unknown_session_returns_404(self, client, make_token):
        response = client.post(
            "/api/v1/session/message",
            json={"session_id": 999999, "content": "你好"},
            headers=_auth(make_token(user_id=7102, tenant_id=1, role="agent")),
        )
        assert response.status_code == 404

    def test_message_runs_agent_graph_and_persists_reply(self, client, db, make_token, monkeypatch):
        from app.agents import graph as graph_module
        from app.config import settings
        from app.crud import session as session_crud
        from app.services import agent_service

        tenant = Tenant(name="回归测试租户")
        db.add(tenant)
        db.commit()
        session = session_crud.create_session(db, tenant_id=tenant.id)
        db.commit()

        fake_graph = _FakeGraph()
        monkeypatch.setattr(settings, "llm_enabled", True)
        monkeypatch.setattr(graph_module, "get_graph", lambda: fake_graph)
        monkeypatch.setattr(agent_service, "_schedule_trace_save", lambda *args, **kwargs: None)

        response = client.post(
            "/api/v1/session/message",
            json={"session_id": session.id, "content": "已发货的订单还能退款吗"},
            headers=_auth(make_token(user_id=7103, tenant_id=tenant.id, role="agent")),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["role"] == "assistant"
        assert body["content"] == "订单发货后 7 天内可申请退款。"
        assert isinstance(body["message_id"], int)
        assert body["sources"] == [
            {"doc": "售后政策.md", "chunk_index": 2, "text": "发货后 7 天内可申请退款"},
        ]
        assert body["metadata"]["intent"] == "knowledge_consult"
        assert body["metadata"]["degraded"] is False

        assert len(fake_graph.states) == 1
        state = fake_graph.states[0]
        assert state["session_id"] == session.id
        assert state["tenant_id"] == tenant.id
        assert state["messages"][-1] == {"role": "user", "content": "已发货的订单还能退款吗"}

        stored = db.execute(
            select(Message).where(Message.session_id == session.id).order_by(Message.id)
        ).scalars().all()
        assert [(m.role, m.content) for m in stored] == [
            ("user", "已发货的订单还能退款吗"),
            ("assistant", "订单发货后 7 天内可申请退款。"),
        ]
        assert stored[1].id == body["message_id"]

        db.expire_all()
        refreshed = db.get(SessionModel, session.id)
        assert refreshed.status == "agent_handling"
        assert refreshed.effective_reply is True
