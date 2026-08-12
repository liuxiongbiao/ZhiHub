"""
backend/tests/contracts/test_ops_enhancement.py

职责: 测试 #20-#26 运营增强 7 项契约

测试内容:
  - TestWidgetBrand: POST /widget 更新品牌 + GET /widget/embed-code 嵌入代码
  - TestAnswerFeedback: POST /message/{id}/feedback 👍/👎 + 去重 409 + 跨租户 404
  - TestQuickReply: GET/POST /quick-replies + 租户隔离
  - TestAngryVipPriority: list_sessions angry 情绪优先排序
  - TestClosedReopen: CLOSED 24h 内续聊重开 + 超期拒绝
  - TestTypingEvent: WS typing 事件广播给同租户坐席
  - TestMultiModelBilling: incr_usage_for_tenant 读 tenant.default_model 按权重折算
"""

import asyncio
import datetime

import pytest
from sqlalchemy import text

from app.crud import session as session_crud
from app.crud import tenant as tenant_crud
from app.models.tenant import Tenant


# ═══════════════════════════════════════════════════════════
# #20 Widget 品牌定制（PRD 3.8）
# ═══════════════════════════════════════════════════════════
class TestWidgetBrand:
    """Widget 品牌配置 + 嵌入代码契约。"""

    def test_update_widget_config(self, client, db, make_token):
        """admin POST /widget 更新主题色/欢迎语 -> 200 返回完整配置。"""
        tenant = tenant_crud.create_tenant(db, name="w1", plan_type="basic")
        db.commit()
        token = make_token(tenant_id=tenant.id, role="admin")

        r = client.post(
            "/api/v1/tenant/widget",
            json={"theme_color": "#ff0000", "welcome_text": "你好，有什么可以帮你？"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["theme_color"] == "#ff0000"
        assert body["welcome_text"] == "你好，有什么可以帮你？"
        assert body["tenant_id"] == tenant.id

    def test_update_widget_incremental(self, client, db, make_token):
        """增量更新: 只传 logo_url，其余字段保持原值。"""
        tenant = tenant_crud.create_tenant(db, name="w2", plan_type="basic")
        db.commit()
        token = make_token(tenant_id=tenant.id, role="admin")
        headers = {"Authorization": f"Bearer {token}"}

        # 先设主题色
        client.post("/api/v1/tenant/widget", json={"theme_color": "#00ff00"}, headers=headers)
        # 再只改 logo_url
        r = client.post(
            "/api/v1/tenant/widget",
            json={"logo_url": "https://cdn.example.com/logo.png"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["theme_color"] == "#00ff00"  # 保持
        assert r.json()["logo_url"] == "https://cdn.example.com/logo.png"

    def test_get_embed_code(self, client, db, make_token):
        """GET /widget/embed-code 返回自托管 widget.js + API Key 占位 + 主题色。"""
        tenant = tenant_crud.create_tenant(db, name="w3", plan_type="basic")
        db.commit()
        token = make_token(tenant_id=tenant.id, role="admin")

        r = client.get(
            "/api/v1/tenant/widget/embed-code",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        code = r.json()["embed_code"]
        assert '/widget.js' in code
        assert 'data-api-key' in code
        assert 'data-endpoint="/api/v1/widget"' in code
        assert 'data-theme-color="#1890ff"' in code

    def test_non_admin_widget_forbidden(self, client, db, make_token):
        """非 admin 角色 POST /widget -> 403。"""
        tenant = tenant_crud.create_tenant(db, name="w4", plan_type="basic")
        db.commit()
        token = make_token(tenant_id=tenant.id, role="agent")

        r = client.post(
            "/api/v1/tenant/widget",
            json={"theme_color": "#000000"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════
# #21 回答反馈（PRD 3.8）
# ═══════════════════════════════════════════════════════════
class TestAnswerFeedback:
    """回答反馈 👍/👎 + 去重 + 租户隔离契约。"""

    def _setup_session_msg(self, db, tenant_id=1):
        s = session_crud.create_session(db, tenant_id=tenant_id)
        msg = session_crud.create_message(db, session_id=s.id, role="assistant", content="回复")
        db.commit()
        return msg

    def test_like_feedback(self, client, db, make_token):
        """score=1 -> bad_case=False。"""
        msg = self._setup_session_msg(db)
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/message/{msg.id}/feedback",
            json={"score": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["score"] == 1
        assert r.json()["bad_case"] is False

    def test_dislike_marks_bad_case(self, client, db, make_token):
        """score=-1 -> bad_case=True + comment。"""
        msg = self._setup_session_msg(db)
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/message/{msg.id}/feedback",
            json={"score": -1, "comment": "答非所问"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["score"] == -1
        assert r.json()["bad_case"] is True

    def test_duplicate_feedback_409(self, client, db, make_token):
        """同一消息二次反馈 -> 409。"""
        msg = self._setup_session_msg(db)
        token = make_token(tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}

        r1 = client.post(f"/api/v1/session/message/{msg.id}/feedback", json={"score": 1}, headers=headers)
        assert r1.status_code == 201
        r2 = client.post(f"/api/v1/session/message/{msg.id}/feedback", json={"score": -1}, headers=headers)
        assert r2.status_code == 409

    def test_cross_tenant_message_404(self, client, db, make_token):
        """租户2 访问租户1 的消息 -> 404。"""
        msg = self._setup_session_msg(db, tenant_id=1)
        token = make_token(tenant_id=2, role="agent")

        r = client.post(
            f"/api/v1/session/message/{msg.id}/feedback",
            json={"score": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    def test_zero_score_rejected(self, client, db, make_token):
        """score=0 -> 422（仅允许 ±1）。"""
        msg = self._setup_session_msg(db)
        token = make_token(tenant_id=1, role="agent")

        r = client.post(
            f"/api/v1/session/message/{msg.id}/feedback",
            json={"score": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════
# #22 快捷回复（PRD 3.7）
# ═══════════════════════════════════════════════════════════
class TestQuickReply:
    """快捷回复模板 CRUD + 租户隔离契约。"""

    def test_create_and_list(self, client, db, make_token):
        """POST 创建 -> GET 列表含创建项。"""
        token = make_token(tenant_id=1, role="agent")
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post(
            "/api/v1/session/quick-replies",
            json={"content": "退款已受理", "shortcut": "/refund"},
            headers=headers,
        )
        assert r.status_code == 201
        assert r.json()["content"] == "退款已受理"
        assert r.json()["shortcut"] == "/refund"

        r2 = client.get("/api/v1/session/quick-replies", headers=headers)
        assert r2.status_code == 200
        assert len(r2.json()) >= 1

    def test_tenant_isolation(self, client, db, make_token):
        """租户1 的模板不出现在租户2 列表。"""
        token1 = make_token(tenant_id=1, role="agent")
        token2 = make_token(tenant_id=2, role="agent")

        client.post(
            "/api/v1/session/quick-replies",
            json={"content": "租户1专属"},
            headers={"Authorization": f"Bearer {token1}"},
        )
        r2 = client.get(
            "/api/v1/session/quick-replies",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert r2.status_code == 200
        # 租户2 列表不含租户1 的内容
        contents = [item["content"] for item in r2.json()]
        assert "租户1专属" not in contents


# ═══════════════════════════════════════════════════════════
# #23 愤怒VIP优先排序
# ═══════════════════════════════════════════════════════════
def _set_created_at(db, session_id, hours_ago):
    """用 raw SQL 设置 created_at 为 N 小时前（绕过 server_default）。"""
    old = datetime.datetime.utcnow() - datetime.timedelta(hours=hours_ago)
    db.execute(text("UPDATE sessions SET created_at=:t WHERE id=:id"), {"t": old, "id": session_id})


class TestAngryVipPriority:
    """list_sessions angry 情绪优先排序契约。"""

    def test_angry_sessions_first(self, db):
        """angry 会话排在 normal/unhappy 之前，即使 created_at 更旧。"""
        # 创建顺序: angry(最旧) -> unhappy -> normal(最新)
        s_angry = session_crud.create_session(db, tenant_id=1)
        session_crud.update_intent(db, s_angry, intent="ticket_query", sentiment="angry")

        s_unhappy = session_crud.create_session(db, tenant_id=1)
        session_crud.update_intent(db, s_unhappy, intent="order_query", sentiment="unhappy")

        s_normal = session_crud.create_session(db, tenant_id=1)
        db.commit()

        # created_at: angry 最旧，normal 最新
        _set_created_at(db, s_angry.id, 3)
        _set_created_at(db, s_unhappy.id, 2)
        _set_created_at(db, s_normal.id, 1)
        db.commit()

        items, total = session_crud.list_sessions(db, tenant_id=1, page=1, page_size=10)
        assert total == 3
        # angry 排第一（尽管 created_at 最旧）
        assert items[0]["session_id"] == s_angry.id
        assert items[0]["sentiment"] == "angry"

    def test_no_angry_falls_back_to_time(self, db):
        """无 angry 时按 created_at 倒序（最新在前）。"""
        s1 = session_crud.create_session(db, tenant_id=1)
        session_crud.update_intent(db, s1, intent="x", sentiment="normal")
        s2 = session_crud.create_session(db, tenant_id=1)
        session_crud.update_intent(db, s2, intent="x", sentiment="unhappy")
        db.commit()

        _set_created_at(db, s1.id, 2)  # 旧
        _set_created_at(db, s2.id, 1)  # 新
        db.commit()

        items, _ = session_crud.list_sessions(db, tenant_id=1, page=1, page_size=10)
        # 无 angry -> 按 created_at desc，s2(新) 排前
        assert items[0]["session_id"] == s2.id


# ═══════════════════════════════════════════════════════════
# #24 会话关闭24h续聊
# ═══════════════════════════════════════════════════════════
class TestClosedReopen:
    """CLOSED 会话续聊窗口（24h）契约。"""

    def test_closed_reopen_within_24h(self, db):
        """CLOSED 且 updated_at 在 24h 内 -> 重开为 agent_handling。"""
        from app.services.session_service import handle_closed_reply

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "closed")
        db.commit()

        result = handle_closed_reply(db, session_id=s.id, tenant_id=1)
        assert result["reopened"] is True
        assert result["status"] == "agent_handling"
        db.refresh(s)
        assert s.status == "agent_handling"

    def test_closed_reopen_expired(self, db):
        """CLOSED 且 updated_at 超过 24h -> SessionReopenExpired。"""
        from app.services.session_service import handle_closed_reply, SessionReopenExpired

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "closed")
        db.commit()

        old = datetime.datetime.utcnow() - datetime.timedelta(hours=25)
        db.execute(text("UPDATE sessions SET updated_at=:t WHERE id=:id"), {"t": old, "id": s.id})
        db.commit()

        with pytest.raises(SessionReopenExpired):
            handle_closed_reply(db, session_id=s.id, tenant_id=1)

    def test_non_closed_no_reopen(self, db):
        """非 closed 状态 -> 无需重开，返回 reopened=False。"""
        from app.services.session_service import handle_closed_reply

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "agent_handling")
        db.commit()

        result = handle_closed_reply(db, session_id=s.id, tenant_id=1)
        assert result["reopened"] is False
        assert result["reason"] == "not_closed"

    def test_reopen_unknown_session_404(self, db):
        """不存在的会话 -> SessionNotFound。"""
        from app.services.session_service import handle_closed_reply, SessionNotFound

        with pytest.raises(SessionNotFound):
            handle_closed_reply(db, session_id=99999, tenant_id=1)


# ═══════════════════════════════════════════════════════════
# #25 输入状态（typing 事件）
# ═══════════════════════════════════════════════════════════
class TestTypingEvent:
    """WS typing 事件广播契约。"""

    def test_typing_event_broadcasts(self, monkeypatch):
        """typing 事件 -> 广播给同租户坐席。"""
        from app.api.v1.ws import _handle_client_message
        from app.core.ws_manager import manager

        calls: list[tuple[int, dict]] = []

        async def fake_broadcast(tenant_id: int, message: dict) -> None:
            calls.append((tenant_id, message))

        monkeypatch.setattr(manager, "broadcast_to_tenant", fake_broadcast)

        asyncio.run(
            _handle_client_message(
                '{"type":"typing","session_id":123,"is_typing":true}', 1
            )
        )
        assert len(calls) == 1
        assert calls[0][0] == 1
        assert calls[0][1]["type"] == "typing"
        assert calls[0][1]["is_typing"] is True

    def test_non_typing_ignored(self, monkeypatch):
        """非 typing 消息（心跳）-> 不广播。"""
        from app.api.v1.ws import _handle_client_message
        from app.core.ws_manager import manager

        calls: list[tuple[int, dict]] = []

        async def fake_broadcast(tenant_id: int, message: dict) -> None:
            calls.append((tenant_id, message))

        monkeypatch.setattr(manager, "broadcast_to_tenant", fake_broadcast)

        asyncio.run(_handle_client_message("heartbeat", 1))
        asyncio.run(_handle_client_message('{"type":"ping"}', 1))
        assert len(calls) == 0

    def test_invalid_json_ignored(self, monkeypatch):
        """非 JSON 文本 -> 静默忽略，不广播。"""
        from app.api.v1.ws import _handle_client_message
        from app.core.ws_manager import manager

        calls: list[tuple[int, dict]] = []

        async def fake_broadcast(tenant_id: int, message: dict) -> None:
            calls.append((tenant_id, message))

        monkeypatch.setattr(manager, "broadcast_to_tenant", fake_broadcast)

        asyncio.run(_handle_client_message("not-json", 1))
        assert len(calls) == 0


# ═══════════════════════════════════════════════════════════
# #26 多模型计费
# ═══════════════════════════════════════════════════════════
class TestMultiModelBilling:
    """按 tenant.default_model 折算计费配额契约。"""

    def test_incr_usage_for_tenant_weighted(self, db, monkeypatch):
        """tenant.default_model 权重 2.0 -> 计费翻倍。"""
        from app.services.billing_service import incr_usage_for_tenant
        from app.config import settings

        tenant = tenant_crud.create_tenant(db, name="b1", plan_type="pro")
        tenant.default_model = "model-x"
        db.flush()

        monkeypatch.setattr(settings, "model_weights", '{"model-x": 2.0}')

        r1 = incr_usage_for_tenant(db, tenant.id)
        assert r1["count"] == 2.0
        r2 = incr_usage_for_tenant(db, tenant.id)
        assert r2["count"] == 4.0

    def test_incr_usage_for_tenant_default_weight(self, db):
        """default_model 未配权重 -> 默认 1.0。"""
        from app.services.billing_service import incr_usage_for_tenant

        tenant = tenant_crud.create_tenant(db, name="b2", plan_type="pro")
        tenant.default_model = "deepseek/deepseek-chat"
        db.flush()

        r = incr_usage_for_tenant(db, tenant.id)
        assert r["count"] == 1.0

    def test_incr_usage_for_tenant_fallback(self, db):
        """租户不存在 -> 降级用默认模型（权重 1.0）。"""
        from app.services.billing_service import incr_usage_for_tenant

        r = incr_usage_for_tenant(db, 99999)
        assert r["count"] >= 1.0
