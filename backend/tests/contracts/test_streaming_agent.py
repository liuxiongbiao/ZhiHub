"""
backend/tests/contracts/test_streaming_agent.py

职责: 测试流式端点 /session/message/stream 复用 Agent 主链路（修复审查报告 6.2）。
对应 feature: 013-prd-coverage-gap-closure / US6 / T013

核心契约: 流式响应内容必须来自 agent_service.handle_message（经路由/检索/工具/安全/计费），
而非直接调 litellm.completion（裸 LLM）。
"""

import json
import re

from app.models.session import Session


def _make_session(db, *, tenant_id=1, status="agent_handling"):
    s = Session(tenant_id=tenant_id, status=status, end_user_id="eu-test")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


class TestStreamingReusesAgent:
    def test_stream_content_comes_from_agent(self, client, db, make_token, monkeypatch):
        """流式内容来自 agent_service.handle_message，而非裸 litellm。"""
        s = _make_session(db)
        token = make_token(user_id=1, tenant_id=1, role="admin")

        called = {"n": 0}

        def _fake_handle(db, *, session_id, tenant_id, user_message):
            called["n"] += 1
            return {
                "message_id": 42,
                "role": "assistant",
                "content": "AGENT_REPLY",
                "sources": [],
                "metadata": None,
            }

        from app.services import agent_service
        monkeypatch.setattr(agent_service, "handle_message", _fake_handle)

        resp = client.post(
            "/api/v1/session/message/stream",
            json={"session_id": s.id, "content": "你好"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # handle_message 必须被调用（证明走 Agent 主链路）
        assert called["n"] == 1
        # SSE 内容应包含 AGENT_REPLY 的字符（来自 Agent，非裸 LLM）
        body = resp.text
        # SSE 逐字输出，解析 chunk 重构完整内容
        chunks = re.findall(r'data: ({"chunk": "."})', body)
        reconstructed = "".join(json.loads(c)["chunk"] for c in chunks)
        assert reconstructed == "AGENT_REPLY"
        assert "[DONE]" in body

    def test_stream_does_not_call_litellm_directly(self, client, db, make_token, monkeypatch):
        """流式端点不直接调 litellm.completion（裸 LLM）。"""
        s = _make_session(db)
        token = make_token(user_id=1, tenant_id=1, role="admin")

        litellm_called = {"n": 0}
        import litellm
        monkeypatch.setattr(litellm, "completion", lambda **kw: litellm_called.__setitem__("n", litellm_called["n"] + 1))

        from app.services import agent_service
        monkeypatch.setattr(agent_service, "handle_message", lambda db_, **kw: {
            "message_id": 1, "role": "assistant", "content": "OK", "sources": [], "metadata": None})

        client.post(
            "/api/v1/session/message/stream",
            json={"session_id": s.id, "content": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # 流式端点不得直接调 litellm.completion（应由 agent_service 内部经网关调用）
        assert litellm_called["n"] == 0

    def test_stream_session_not_found_404(self, client, make_token):
        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.post(
            "/api/v1/session/message/stream",
            json={"session_id": 999999, "content": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    def test_stream_emits_message_id_event(self, client, db, make_token, monkeypatch):
        """SSE 流结束前发送 message_id 事件，前端可据此关联落库的 assistant 消息（前后端一致契约）。"""
        from app.models.session import Session
        s = Session(tenant_id=1, status="agent_handling", end_user_id="eu-mid")
        db.add(s)
        db.commit()
        db.refresh(s)
        token = make_token(user_id=1, tenant_id=1, role="admin")

        from app.services import agent_service
        monkeypatch.setattr(agent_service, "handle_message", lambda **kw: {
            "message_id": 99, "role": "assistant", "content": "OK",
            "sources": [], "metadata": None,
        })

        resp = client.post(
            "/api/v1/session/message/stream",
            json={"session_id": s.id, "content": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert '"message_id": 99' in resp.text
        assert '"chunk": "O"' in resp.text
        assert "[DONE]" in resp.text
