"""
backend/tests/contracts/test_websocket.py

职责: 测试 PRD 3.7 WebSocket 实时推送坐席工作台契约
对应日程: PRD 3.7 -- WebSocket 实时推送

测试内容:
  - TestWSConnect: 有效 agent/admin token 成功建立 WS 连接
  - TestWSAuth: 无效/缺失/越权 token 连接被拒（close 1008）
  - TestWSBroadcast: 连接后坐席认领会话 -> 同租户坐席收到 session_claimed 推送
  - TestWSTenantIsolation: 推送按 tenant_id 隔离，不跨租户泄漏

关键技术点:
  - WS 鉴权用 JWT query 参数 (?token=xxx): 浏览器原生 WS 不支持自定义 header，
    query 参数是通用做法（wss:// 需配合 TLS 防止 token 泄漏到中间人）
  - TestClient.websocket_connect 是同步阻塞接口，底层走 anyio portal 事件循环；
    broadcast_to_tenant_sync 用 run_coroutine_threadsafe 把广播协程提交到该循环，
    主线程随后 receive_json() 触发循环运行，广播协程得以执行并 send_json
  - 单例 manager 跨测试污染: autouse fixture 每次重置 _connections + _loop，
    保证每测试拿到干净连接表且重新捕获本测试的 portal 循环
  - 租户隔离验证技巧: 触发租户1 广播后，主动给租户2 发一条 ping，receive 应收到
    ping 而非 session_claimed；若隔离失效，租户2 会先收到泄漏的 session_claimed
"""

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.ws_manager import manager


@pytest.fixture(autouse=True)
def _reset_ws_manager():
    """每测试重置 manager 单例状态（连接表 + 缓存循环），避免跨测试污染。

    TestClient 每次新建 portal 事件循环，若不重置 _loop，旧循环已关闭，
    broadcast_to_tenant_sync 的 run_coroutine_threadsafe 会失败。
    """
    manager._connections.clear()
    manager._loop = None
    yield
    manager._connections.clear()
    manager._loop = None


# ═══════════════════════════════════════════════════════════
# 连接成功
# ═══════════════════════════════════════════════════════════
class TestWSConnect:
    """有效 token 建立 WS 连接契约。"""

    def test_agent_connect(self, client, make_token):
        """agent 角色 token 成功连接（进入 with 不抛异常即 accept 成功）。"""
        token = make_token(user_id=10, tenant_id=1, role="agent")
        with client.websocket_connect(f"/api/v1/ws/agent?token={token}"):
            pass

    def test_admin_connect(self, client, make_token):
        """admin 角色 token 成功连接。"""
        token = make_token(user_id=11, tenant_id=1, role="admin")
        with client.websocket_connect(f"/api/v1/ws/agent?token={token}"):
            pass


# ═══════════════════════════════════════════════════════════
# 鉴权失败
# ═══════════════════════════════════════════════════════════
class TestWSAuth:
    """无效/越权 token 连接被拒契约。"""

    def test_invalid_token_rejected(self, client):
        """无效 token -> 连接被拒（close 1008）。"""
        with pytest.raises((WebSocketDisconnect, Exception)):
            with client.websocket_connect("/api/v1/ws/agent?token=not-a-jwt"):
                pass

    def test_missing_token_rejected(self, client):
        """缺失 token 参数 -> 连接被拒。"""
        with pytest.raises((WebSocketDisconnect, Exception)):
            with client.websocket_connect("/api/v1/ws/agent"):
                pass

    def test_enduser_role_rejected(self, client, make_token):
        """非 agent/admin 角色（end_user）-> 连接被拒。"""
        token = make_token(user_id=20, tenant_id=1, role="end_user")
        with pytest.raises((WebSocketDisconnect, Exception)):
            with client.websocket_connect(f"/api/v1/ws/agent?token={token}"):
                pass


# ═══════════════════════════════════════════════════════════
# 广播推送
# ═══════════════════════════════════════════════════════════
class TestWSBroadcast:
    """坐席认领会话后，同租户在线坐席收到实时推送契约。"""

    def test_claim_triggers_broadcast(self, client, db, make_token):
        """坐席B 认领会话 -> 已连接的坐席A 收到 session_claimed 推送。

        流程:
          1. 造一个 waiting_human 会话
          2. 坐席A WS 连接（tenant=1）
          3. with 块内调 claim_session（坐席B 认领）-> 触发 broadcast_to_tenant_sync
          4. 坐席A receive_json 收到 session_claimed 消息
        """
        from app.crud import session as session_crud
        from app.services.human_service import claim_session

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()

        token_a = make_token(user_id=100, tenant_id=1, role="agent")
        with client.websocket_connect(f"/api/v1/ws/agent?token={token_a}") as ws:
            # 同步认领 -> 内部 broadcast_to_tenant_sync 提交广播协程到 portal 循环
            claim_session(db, session_id=s.id, agent_id=200, tenant_id=1)
            # receive_json 触发循环运行，广播协程执行 send_json
            msg = ws.receive_json()

        assert msg["event"] == "session_claimed"
        assert msg["session_id"] == s.id
        assert msg["agent_id"] == 200

    def test_broadcast_tenant_isolation(self, client, db, make_token):
        """租户1 认领 -> 租户2 在线坐席收不到推送（租户隔离）。

        验证技巧: 触发租户1 广播后，主动给租户2 发一条 ping，receive 应收到 ping
        而非 session_claimed。若隔离失效，租户2 会先收到泄漏的 session_claimed。
        """
        from app.crud import session as session_crud
        from app.services.human_service import claim_session

        s = session_crud.create_session(db, tenant_id=1)
        session_crud.update_status(db, s, "waiting_human")
        db.commit()

        token_t2 = make_token(user_id=300, tenant_id=2, role="agent")
        with client.websocket_connect(f"/api/v1/ws/agent?token={token_t2}") as ws:
            # 租户1 认领 -> 广播只发 tenant=1，不应到达租户2
            claim_session(db, session_id=s.id, agent_id=200, tenant_id=1)
            # 主动给租户2 发 ping
            manager.broadcast_to_tenant_sync(2, {"event": "ping"})
            # 隔离正确: 收到的是 ping，而非泄漏的 session_claimed
            msg = ws.receive_json()
        assert msg["event"] == "ping"
