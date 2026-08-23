"""契约测试： Widget 部署闭环。

覆盖:
  - TestEmbedCodeDeployable: 嵌入代码必须使用可配置绝对公共地址（PUBLIC_BASE_URL），
    开发环境给默认值，生产缺失显式报错；WebSocket 地址按 scheme 推导（http->ws / https->wss）。
  - TestWidgetConfigQuery: GET /tenant/widget 与 GET /tenant/widget/domains 查询契约。
  - TestApiKeyRotation: 轮换后旧 Key 立即失效。
  - TestEndUserEvents: 终端用户 WS 事件契约（waiting_human / human_claimed /
    human_returned / session_ended），统一 type/payload envelope，agent_message 兼容不动。
  - TestWidgetCors: Widget 公共通道按租户域名白名单动态放行 CORS（不全局放开）。
"""

import pytest

from app.crud import session as session_crud
from app.crud import tenant as tenant_crud
from app.models.tenant import Tenant


def _mk_tenant(db, *, domains="example.com"):
    from app.services import tenant_service
    tenant = tenant_crud.create_tenant(db, name="p02-tenant", plan_type="basic")
    db.commit()
    key = tenant_service.generate_widget_api_key(db, tenant.id)["api_key"]
    tenant_service.set_widget_allowed_domains(db, tenant.id, domains.split(","))
    return tenant, key


def _admin_headers(make_token, tenant_id):
    return {"Authorization": f"Bearer {make_token(tenant_id=tenant_id, role='admin')}"}


# ═══════════════════════════════════════════════════════════
# 目标二：可部署嵌入代码
# ═══════════════════════════════════════════════════════════
class TestEmbedCodeDeployable:
    def test_embed_code_uses_configured_public_base_url(self, client, db, make_token, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "public_base_url", "https://chat.example.com")
        tenant, _ = _mk_tenant(db)
        r = client.get("/api/v1/tenant/widget/embed-code", headers=_admin_headers(make_token, tenant.id))
        assert r.status_code == 200, r.text
        code = r.json()["embed_code"]
        assert 'src="https://chat.example.com/widget.js"' in code
        assert 'data-endpoint="https://chat.example.com/api/v1/widget"' in code
        # WebSocket 地址按 scheme 推导: https → wss
        assert 'data-ws-endpoint="wss://chat.example.com/api/v1/ws"' in code
        # 不允许 localhost 出现在生产地址
        assert "localhost" not in code

    def test_embed_code_dev_default_localhost(self, client, db, make_token, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "public_base_url", "")
        monkeypatch.setattr(settings, "app_env", "development")
        tenant, _ = _mk_tenant(db)
        r = client.get("/api/v1/tenant/widget/embed-code", headers=_admin_headers(make_token, tenant.id))
        assert r.status_code == 200, r.text
        code = r.json()["embed_code"]
        assert 'src="http://localhost:5173/widget.js"' in code
        assert 'data-ws-endpoint="ws://localhost:5173/api/v1/ws"' in code

    def test_embed_code_production_missing_config_rejected(self, client, db, make_token, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "public_base_url", "")
        monkeypatch.setattr(settings, "app_env", "production")
        tenant, _ = _mk_tenant(db)
        r = client.get("/api/v1/tenant/widget/embed-code", headers=_admin_headers(make_token, tenant.id))
        # 生产缺失公共地址必须显式报错，不能退回 localhost 假闭环
        assert r.status_code == 409
        assert "PUBLIC_BASE_URL" in r.json()["detail"]

    def test_embed_code_http_config_maps_to_ws(self, client, db, make_token, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "public_base_url", "http://chat.internal.example")
        tenant, _ = _mk_tenant(db)
        r = client.get("/api/v1/tenant/widget/embed-code", headers=_admin_headers(make_token, tenant.id))
        code = r.json()["embed_code"]
        assert 'data-ws-endpoint="ws://chat.internal.example/api/v1/ws"' in code


# ═══════════════════════════════════════════════════════════
# 目标一：配置查询契约
# ═══════════════════════════════════════════════════════════
class TestWidgetConfigQuery:
    def test_get_widget_config_returns_brand_and_domains(self, client, db, make_token):
        tenant, _ = _mk_tenant(db, domains="example.com,shop.example.com")
        from app.services import tenant_service
        tenant_service.update_widget_config(
            db, tenant.id, theme_color="#123456", welcome_text="欢迎光临",
        )
        r = client.get("/api/v1/tenant/widget", headers=_admin_headers(make_token, tenant.id))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["theme_color"] == "#123456"
        assert body["welcome_text"] == "欢迎光临"
        assert sorted(body["domains"]) == ["example.com", "shop.example.com"]

    def test_get_domains_after_put(self, client, db, make_token):
        tenant, _ = _mk_tenant(db, domains="old.example.com")
        headers = _admin_headers(make_token, tenant.id)
        r_put = client.put(
            "/api/v1/tenant/widget/domains",
            json={"domains": ["a.example.com", "b.example.com"]},
            headers=headers,
        )
        assert r_put.status_code == 200, r_put.text
        r = client.get("/api/v1/tenant/widget/domains", headers=headers)
        assert r.status_code == 200
        assert sorted(r.json()["domains"]) == ["a.example.com", "b.example.com"]

    def test_api_key_never_return_by_query(self, client, db, make_token):
        """GET 配置/域名接口不得回显 API Key 明文或哈希。"""
        tenant, key = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        for path in ("/api/v1/tenant/widget", "/api/v1/tenant/widget/domains"):
            body = client.get(path, headers=headers).json()
            assert "api_key" not in body
            assert key not in str(body)


class TestWidgetTokenExpiry:
    """token 过期契约: HTTP 401 + WS close 1008。"""

    def _mk_expired_widget_token(self, tenant_id, session_id):
        from app.core.security import create_access_token
        return create_access_token(
            subject=f"w{session_id}",
            extra_claims={"tenant_id": tenant_id, "role": "widget", "session_id": session_id},
            expires_minutes=-1,  # 已过期
        )

    def test_expired_token_http_401(self, client, db, make_token):
        tenant, key = _mk_tenant(db)
        sess = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
        ).json()
        expired = self._mk_expired_widget_token(tenant.id, sess["session_id"])
        r = client.post(
            f"/api/v1/widget/{sess['session_id']}/message",
            headers={"Authorization": f"Bearer {expired}"},
            json={"session_id": sess["session_id"], "content": "你好"},
        )
        assert r.status_code == 401

    def test_expired_token_ws_close_1008(self, client, db):
        tenant, key = _mk_tenant(db)
        sess = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
        ).json()
        expired = self._mk_expired_widget_token(tenant.id, sess["session_id"])
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/api/v1/ws/session/{sess['session_id']}?token={expired}"
            ) as websocket:
                websocket.receive_json()

    def test_widget_token_of_other_session_ws_rejected(self, client, db):
        """会话令牌与路径 session_id 不一致 -> close 1008（租户与 session token 隔离）。"""
        tenant, key = _mk_tenant(db)
        s1 = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
        ).json()
        s2 = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
        ).json()
        # 用 s1 的 token 连 s2 的会话通道 → 拒绝
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/api/v1/ws/session/{s2['session_id']}?token={s1['token']}"
            ) as websocket:
                websocket.receive_json()


class TestApiKeyRotation:
    def test_rotation_invalidates_old_key(self, client, db, make_token):
        from app.services import tenant_service
        tenant, old_key = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        r = client.post("/api/v1/tenant/widget/api-key", headers=headers)
        assert r.status_code == 200, r.text
        new_key = r.json()["api_key"]
        assert new_key != old_key
        # 旧 Key 立即失效
        resp_old = client.post(
            "/api/v1/widget/session",
            json={"api_key": old_key, "origin": "https://example.com"},
        )
        assert resp_old.status_code == 401
        # 新 Key 可用
        resp_new = client.post(
            "/api/v1/widget/session",
            json={"api_key": new_key, "origin": "https://example.com"},
        )
        assert resp_new.status_code == 201


# ═══════════════════════════════════════════════════════════
# 目标三：终端用户事件契约
# ═══════════════════════════════════════════════════════════
class TestEndUserEvents:
    def test_waiting_human_broadcasts_to_session(self, db, monkeypatch):
        from app.core.ws_manager import manager
        from app.services.agent_service import _notify_new_session

        calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            manager, "broadcast_to_session_sync",
            lambda session_id, message: calls.append((session_id, message)),
        )
        _notify_new_session(tenant_id=1, session_id=42)
        session_events = [m for sid, m in calls if sid == 42]
        assert session_events, "转人工必须向终端用户会话通道广播事件"
        ev = session_events[0]
        assert ev["type"] == "waiting_human"
        assert ev["payload"]["session_id"] == 42

    def test_claim_notifies_end_user(self, db, monkeypatch):
        from app.core.ws_manager import manager
        from app.services.human_service import claim_session

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()

        calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            manager, "broadcast_to_session_sync",
            lambda session_id, message: calls.append((session_id, message)),
        )
        claim_session(db, session_id=s.id, agent_id=101, tenant_id=1)
        events = [m for sid, m in calls if sid == s.id]
        assert events and events[0]["type"] == "human_claimed"
        assert events[0]["payload"]["session_id"] == s.id
        # envelope 契约: type + payload
        assert "payload" in events[0]

    def test_return_to_agent_notifies_end_user(self, db, monkeypatch):
        from app.core.ws_manager import manager
        from app.services.human_service import return_to_agent

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        s.assignee_id = 101
        db.commit()

        calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            manager, "broadcast_to_session_sync",
            lambda session_id, message: calls.append((session_id, message)),
        )
        return_to_agent(db, session_id=s.id, tenant_id=1, agent_id=101)
        events = [m for sid, m in calls if sid == s.id]
        assert events and events[0]["type"] == "human_returned"
        assert events[0]["payload"]["session_id"] == s.id

    def test_session_ended_event_on_idle_cleanup(self, db, monkeypatch):
        import datetime
        from sqlalchemy import text
        from app.tasks.cleanup_tasks import cleanup_idle_sessions
        from app.core.ws_manager import manager

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()
        old = datetime.datetime.utcnow() - datetime.timedelta(minutes=45)
        db.execute(text("UPDATE sessions SET updated_at=:t WHERE id=:id"), {"t": old, "id": s.id})
        db.commit()

        calls: list[tuple[int, dict]] = []
        monkeypatch.setattr(
            manager, "broadcast_to_session_sync",
            lambda session_id, message: calls.append((session_id, message)),
        )
        result = cleanup_idle_sessions(db)
        assert result["resolved"] >= 1
        events = [m for sid, m in calls if sid == s.id]
        assert events and events[0]["type"] == "session_ended"
        assert events[0]["payload"]["session_id"] == s.id


# ═══════════════════════════════════════════════════════════
# 目标二：Widget CORS（按租户白名单动态放行，不全局放开）
# ═══════════════════════════════════════════════════════════
class TestWidgetCors:
    """中间件白名单走进程内 TTL 缓存 + SessionLocal，测试里直接注入缓存集。"""

    @staticmethod
    def _inject_whitelist(monkeypatch, hosts):
        import time as _time
        from app.middlewares import widget_cors_middleware as wcm
        monkeypatch.setattr(
            wcm, "_hosts_cache", {"ts": _time.monotonic() + 9999, "hosts": frozenset(hosts)},
        )

    def test_preflight_allowed_for_whitelisted_origin(self, client, db, monkeypatch):
        self._inject_whitelist(monkeypatch, ["shop.example.com"])
        r = client.options(
            "/api/v1/widget/session",
            headers={
                "Origin": "https://shop.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.status_code in (200, 204)
        assert r.headers.get("access-control-allow-origin") == "https://shop.example.com"

    def test_preflight_allowed_for_whitelisted_subdomain(self, client, db, monkeypatch):
        self._inject_whitelist(monkeypatch, ["example.com"])
        r = client.options(
            "/api/v1/widget/session",
            headers={
                "Origin": "https://shop.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "https://shop.example.com"

    def test_preflight_rejected_for_unknown_origin(self, client, db, monkeypatch):
        self._inject_whitelist(monkeypatch, ["shop.example.com"])
        r = client.options(
            "/api/v1/widget/session",
            headers={
                "Origin": "https://evil.org",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in r.headers

    def test_actual_response_carries_acao_for_whitelisted_origin(self, client, db, monkeypatch):
        self._inject_whitelist(monkeypatch, ["shop.example.com"])
        tenant, key = _mk_tenant(db, domains="shop.example.com")
        r = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
            headers={"Origin": "https://shop.example.com"},
        )
        assert r.status_code == 201, r.text
        assert r.headers.get("access-control-allow-origin") == "https://shop.example.com"

    def test_non_widget_paths_not_affected(self, client, db, monkeypatch):
        self._inject_whitelist(monkeypatch, ["shop.example.com"])
        r = client.get("/health", headers={"Origin": "https://shop.example.com"})
        assert "access-control-allow-origin" not in r.headers
