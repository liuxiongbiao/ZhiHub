"""
批次 3 回归测试：人工接管回复、坐席 presence TTL 与 WebSocket envelope。
"""

import pytest


def _human_session(db, *, assignee_id: int = 101):
    from app.crud import session as session_crud

    session = session_crud.create_session(db, tenant_id=1)
    session_crud.update_status(db, session, "human_handling")
    session.assignee_id = assignee_id
    db.commit()
    return session


class TestAgentMessageHandoff:
    def test_claim_agent_reply_is_persisted_and_pushed_without_agent_execution(
        self, client, db, make_token, monkeypatch,
    ):
        from app.models.message import Message
        from app.services import agent_service
        from app.core.ws_manager import manager

        session = _human_session(db)
        calls: list[tuple[int, dict]] = []
        agent_calls: list[dict] = []
        monkeypatch.setattr(
            manager,
            "broadcast_to_session_sync",
            lambda session_id, message: calls.append((session_id, message)),
            raising=False,
        )
        monkeypatch.setattr(
            agent_service,
            "handle_message",
            lambda **kwargs: agent_calls.append(kwargs),
        )

        token = make_token(user_id=101, tenant_id=1, role="agent")
        response = client.post(
            f"/api/v1/session/{session.id}/agent-message",
            json={"content": "您好，我来为您处理"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "agent"
        message = db.get(Message, body["message_id"])
        assert message is not None and message.role == "agent"
        assert agent_calls == []
        assert calls == [
            (
                session.id,
                {
                    "type": "agent_message",
                    "payload": {
                        "session_id": session.id,
                        "message_id": message.id,
                        "role": "agent",
                        "content": "您好，我来为您处理",
                    },
                },
            )
        ]

    def test_user_message_during_handoff_is_rejected_before_agent_execution(
        self, client, db, make_token, monkeypatch,
    ):
        from app.services import agent_service

        session = _human_session(db)
        agent_calls: list[dict] = []
        monkeypatch.setattr(
            agent_service,
            "handle_message",
            lambda **kwargs: agent_calls.append(kwargs) or {
                "message_id": 999,
                "role": "assistant",
                "content": "不应执行",
                "sources": [],
            },
        )
        token = make_token(user_id=501, tenant_id=1, role="admin")
        response = client.post(
            "/api/v1/session/message",
            json={"session_id": session.id, "content": "还在吗"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 409
        assert "人工客服" in response.json()["detail"]
        assert agent_calls == []

    def test_non_assignee_agent_message_is_rejected(self, client, db, make_token):
        session = _human_session(db, assignee_id=101)
        token = make_token(user_id=202, tenant_id=1, role="agent")
        response = client.post(
            f"/api/v1/session/{session.id}/agent-message",
            json={"content": "越权回复"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert "认领坐席" in response.json()["detail"]

    def test_stream_message_during_handoff_is_rejected_before_streaming(
        self, client, db, make_token,
    ):
        session = _human_session(db)
        token = make_token(user_id=501, tenant_id=1, role="admin")
        response = client.post(
            "/api/v1/session/message/stream",
            json={"session_id": session.id, "content": "继续追问"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 409
        assert "人工客服" in response.json()["detail"]

    def test_non_agent_role_cannot_use_agent_message(self, client, db, make_token):
        session = _human_session(db)
        token = make_token(user_id=101, tenant_id=1, role="end_user")
        response = client.post(
            f"/api/v1/session/{session.id}/agent-message",
            json={"content": "越权回复"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestAgentPresenceTTL:
    def test_expired_presence_is_not_online(self, fake_redis):
        from app.services.session_service import (
            _online_key,
            has_online_agent,
            mark_agent_online,
        )

        mark_agent_online(991, 100)
        key = _online_key(991, 100)
        assert fake_redis.ttl(key) > 0
        fake_redis.expire(key, 0)
        assert has_online_agent(991) is False

    def test_cleanup_removes_presence_without_ttl(self, fake_redis):
        from app.services.session_service import cleanup_expired_agent_presence

        fake_redis.set("agents:online:1:100", "1")
        assert cleanup_expired_agent_presence() == 1
        assert fake_redis.exists("agents:online:1:100") == 0


class TestWebSocketEnvelope:
    def test_end_user_ws_receives_agent_role_message(
        self, client, db, make_token, monkeypatch,
    ):
        from app.core import database
        from app.core.ws_manager import manager

        session = _human_session(db)
        monkeypatch.setattr(database, "SessionLocal", lambda: db)
        manager._session_connections.clear()
        token = make_token(user_id=501, tenant_id=1, role="end_user")
        agent_token = make_token(user_id=101, tenant_id=1, role="agent")

        with client.websocket_connect(
            f"/api/v1/ws/session/{session.id}?token={token}"
        ) as websocket:
            response = client.post(
                f"/api/v1/session/{session.id}/agent-message",
                json={"content": "实时人工回复"},
                headers={"Authorization": f"Bearer {agent_token}"},
            )
            assert response.status_code == 200
            event = websocket.receive_json()

        assert event["type"] == "agent_message"
        assert event["payload"]["role"] == "agent"
        assert event["payload"]["content"] == "实时人工回复"
        manager._session_connections.clear()

    def test_claim_broadcast_uses_type_payload_envelope(self, db, monkeypatch):
        from app.crud import session as session_crud
        from app.core.ws_manager import manager
        from app.services.human_service import claim_session

        session = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, session, "waiting_human")
        db.commit()
        calls: list[dict] = []
        monkeypatch.setattr(
            manager,
            "broadcast_to_tenant_sync",
            lambda tenant_id, message: calls.append(message),
        )

        claim_session(db, session_id=session.id, agent_id=101, tenant_id=1)
        assert calls[0]["type"] == "session_claimed"
        assert calls[0]["payload"]["session_id"] == session.id
        assert calls[0]["payload"]["agent_id"] == 101

    def test_blocklist_failure_logs_and_ws_still_connects(self, client, make_token, monkeypatch):
        from app.api.v1 import ws as ws_api

        monkeypatch.setattr(ws_api, "is_blocklisted", lambda jti: (_ for _ in ()).throw(RuntimeError("redis down")))
        token = make_token(user_id=10, tenant_id=1, role="agent", ws=True)
        with client.websocket_connect(f"/api/v1/ws/agent?token={token}"):
            pass
