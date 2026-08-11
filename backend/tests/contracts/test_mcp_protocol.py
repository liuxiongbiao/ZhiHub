"""
backend/tests/contracts/test_mcp_protocol.py

职责: 测试 006 MCP JSON-RPC 2.0 标准协议信封 + 熔断降级契约
对应日程: 006-mcp-security

测试内容:
  - TestJSONRPCEnevelope: JSON-RPC 2.0 信封结构（成功 result / -32601 方法不存在 /
    -32602 参数无效 / -32600 信封非法 / -32000 权限拒绝 / tenant_id 服务端注入覆盖客户端）
  - TestCircuitBreaker: 熔断三态契约 closed -> open -> half_open -> closed
    （连续5次空结果触发熔断 -32001 + 兜底话术；half_open 3次成功恢复；half_open 失败回 open）
"""

import pytest

from app.mcp_servers.base_server import build_request


# ─── 共享夹具：在测试 DB 中建一张绑定手机号的工单/订单 ───
@pytest.fixture()
def seeded_ticket(db):
    from app.crud.ticket import create_ticket

    t = create_ticket(
        db, tenant_id=1, ticket_no="TK-PROTO01", title="信封测试工单",
        description="contract", priority="high",
    )
    t.phone = "13800000001"
    db.commit()
    return t


@pytest.fixture()
def seeded_order(db):
    from app.models.order import Order

    o = Order(
        tenant_id=1, order_no="ORD-PROTO1", amount=299.0,
        paid_amount=299.0, status="paid", phone="13800000001",
    )
    db.add(o)
    db.commit()
    return o


class TestJSONRPCEnevelope:
    """T011: JSON-RPC 2.0 信封结构契约。"""

    def test_success_response_has_envelope(self, db, seeded_ticket):
        """成功响应 MUST 含 jsonrpc/id/result。"""
        from app.services.mcp_service import call_tool

        env = build_request(
            "get_ticket_status",
            {"ticket_no": "TK-PROTO01", "phone": "13800000001"},
        )
        resp = call_tool("agent", env, tenant_id=1, db=db)

        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == env["id"]  # 请求标识原样回传
        assert "result" in resp
        assert resp["result"]["ticket_no"] == "TK-PROTO01"
        assert "error" not in resp

    def test_method_not_found_returns_32601(self, db):
        """非法方法名 MUST 返回 -32601 Method not found，而非系统异常。"""
        from app.services.mcp_service import call_tool

        env = build_request("nonexistent_tool", {})
        resp = call_tool("admin", env, tenant_id=1, db=db)

        assert resp["error"]["code"] == -32601
        assert "result" not in resp

    def test_invalid_params_missing_phone_returns_32602(self, db, seeded_ticket):
        """参数缺失（缺 phone）MUST 返回 -32602 Invalid params。"""
        from app.services.mcp_service import call_tool

        env = build_request("get_ticket_status", {"ticket_no": "TK-PROTO01"})  # 缺 phone
        resp = call_tool("agent", env, tenant_id=1, db=db)

        assert resp["error"]["code"] == -32602

    def test_invalid_envelope_returns_32600(self, db):
        """信封结构不合法（jsonrpc 版本错）MUST 返回 -32600 Invalid Request。"""
        from app.services.mcp_service import call_tool

        bad_env = {"jsonrpc": "1.0", "id": "x", "method": "get_ticket_status", "params": {}}
        resp = call_tool("admin", bad_env, tenant_id=1, db=db)
        assert resp["error"]["code"] == -32600

    def test_permission_denied_returns_32000(self, db):
        """角色无权调用写操作 MUST 返回 -32000 Permission denied。"""
        from app.services.mcp_service import call_tool

        env = build_request(
            "request_refund",
            {"order_no": "ORD-PROTO1", "amount": 10, "client_request_id": "cid-1"},
        )
        # agent 角色不可调用 request_refund
        resp = call_tool("agent", env, tenant_id=1, db=db)
        assert resp["error"]["code"] == -32000

    def test_tenant_id_injected_overrides_client(self, db, seeded_ticket):
        """FR-012 / SC-006: tenant_id MUST 来自服务端可信来源，覆盖客户端传入值。

        客户端传 tenant_id=999（伪造），服务端注入 tenant_id=1 -> 查不到 999 租户的工单
        -> 返回未查询到匹配信息，证明客户端自报值未生效。
        """
        from app.services.mcp_service import call_tool

        env = build_request(
            "get_ticket_status",
            {"ticket_no": "TK-PROTO01", "phone": "13800000001", "tenant_id": 999},
        )
        resp = call_tool("agent", env, tenant_id=1, db=db)
        # 服务端注入 tenant_id=1 -> 命中 seeded_ticket -> 返回成功
        assert "result" in resp
        assert resp["result"]["ticket_no"] == "TK-PROTO01"

    def test_order_success_envelope(self, db, seeded_order):
        """订单查询成功响应同样遵循信封结构。"""
        from app.services.mcp_service import call_tool

        env = build_request(
            "query_order", {"order_no": "ORD-PROTO1", "phone": "13800000001"},
        )
        resp = call_tool("agent", env, tenant_id=1, db=db)
        assert resp["jsonrpc"] == "2.0"
        assert resp["result"]["order_no"] == "ORD-PROTO1"


# ═══════════════════════════════════════════════════════════
# T018: MCP 熔断降级（User Story 4）
# 契约: mcp-protocol.md / data-model.md circuit breaker
# SC-005: 连续故障达阈值 100% 触发熔断并返回兜底响应；恢复后 100% 自动解除
# ═══════════════════════════════════════════════════════════
class TestCircuitBreaker:
    """熔断三态契约：closed -> open -> half_open -> closed。"""

    @staticmethod
    def _reset(tool_name="get_ticket_status"):
        from app.core import redis as redis_module
        redis_module._redis_client.delete(f"mcp:circuit:{tool_name}")

    def test_circuit_triggers_after_5_empty_results(self, db, monkeypatch):
        """连续5次关键字段空 -> 第6次返回 -32001 + 兜底话术。"""
        from app.mcp_servers import ticket_server
        from app.mcp_servers.base_server import ERR_CIRCUIT_BROKEN
        from app.services.mcp_service import call_tool
        from app.core.circuit_breaker import get_state

        self._reset()

        # 桩：handler 返回空关键字段（模拟下游结构性故障）
        def fake_handler(tool_name, params, *, db=None):
            return {"ticket_no": "", "status": ""}

        monkeypatch.setattr(ticket_server, "handle_ticket_tool", fake_handler)

        env = build_request(
            "get_ticket_status", {"ticket_no": "TK-PROTO01", "phone": "13800000001"},
        )
        # 前5次：返回成功信封（空 result），后台 record_failure 累加
        for _ in range(5):
            resp = call_tool("agent", env, tenant_id=1, db=db)
            assert "result" in resp

        # 第6次：已达熔断阈值 -> -32001 + 兜底话术
        resp = call_tool("agent", env, tenant_id=1, db=db)
        assert resp["error"]["code"] == ERR_CIRCUIT_BROKEN
        # 兜底话术含转人工提示（T020）
        assert "人工" in resp["error"]["message"] or "稍候" in resp["error"]["message"]

        state = get_state("get_ticket_status")
        assert state["state"] == "open"

    def test_circuit_broken_returns_32001_until_recovered(self, db, monkeypatch):
        """熔断期间所有调用返回 -32001（不调 handler）。"""
        from app.mcp_servers import ticket_server
        from app.mcp_servers.base_server import ERR_CIRCUIT_BROKEN
        from app.services.mcp_service import call_tool

        self._reset()

        calls = {"n": 0}

        def fake_handler(tool_name, params, *, db=None):
            calls["n"] += 1
            return {"ticket_no": "", "status": ""}

        monkeypatch.setattr(ticket_server, "handle_ticket_tool", fake_handler)

        env = build_request(
            "get_ticket_status", {"ticket_no": "TK-PROTO01", "phone": "13800000001"},
        )
        for _ in range(5):
            call_tool("agent", env, tenant_id=1, db=db)
        handler_calls_before = calls["n"]
        assert handler_calls_before == 5

        # 熔断中：再调3次，handler 不应被调用（被熔断拦截）
        for _ in range(3):
            resp = call_tool("agent", env, tenant_id=1, db=db)
            assert resp["error"]["code"] == ERR_CIRCUIT_BROKEN
        assert calls["n"] == 5  # handler 未再被调用

    def test_circuit_recovers_after_3_successes(self, db, monkeypatch):
        """half_open 状态下连续3次成功 -> closed（恢复正常调用）。"""
        from app.mcp_servers import ticket_server
        from app.services.mcp_service import call_tool
        from app.core.circuit_breaker import STATE_CLOSED, _save

        self._reset()

        # 先熔断
        def empty_handler(tool_name, params, *, db=None):
            return {"ticket_no": "", "status": ""}

        monkeypatch.setattr(ticket_server, "handle_ticket_tool", empty_handler)
        env = build_request(
            "get_ticket_status", {"ticket_no": "TK-PROTO01", "phone": "13800000001"},
        )
        for _ in range(5):
            call_tool("agent", env, tenant_id=1, db=db)

        # 手动转 half_open（绕过 30s 超时，模拟探测窗口开启）
        _save("get_ticket_status", {
            "state": "half_open", "fail_count": 0, "recover_count": 0,
            "last_failed_at": None, "opened_at": None,
        })

        # 桩改为返回正常数据
        monkeypatch.setattr(
            ticket_server, "handle_ticket_tool",
            lambda tool_name, params, *, db=None: {"ticket_no": "TK-PROTO01", "status": "open"},
        )
        for _ in range(3):
            resp = call_tool("agent", env, tenant_id=1, db=db)
            assert "result" in resp

        from app.core.circuit_breaker import get_state
        assert get_state("get_ticket_status")["state"] == STATE_CLOSED

    def test_circuit_half_open_failure_reopens(self, db, monkeypatch):
        """half_open 期间任意失败 -> 回到 open（重置计时）。"""
        from app.mcp_servers import ticket_server
        from app.services.mcp_service import call_tool
        from app.core.circuit_breaker import STATE_OPEN, _save, get_state

        self._reset()
        _save("get_ticket_status", {
            "state": "half_open", "fail_count": 0, "recover_count": 0,
            "last_failed_at": None, "opened_at": None,
        })

        def empty_handler(tool_name, params, *, db=None):
            return {"ticket_no": "", "status": ""}

        monkeypatch.setattr(ticket_server, "handle_ticket_tool", empty_handler)
        env = build_request(
            "get_ticket_status", {"ticket_no": "TK-PROTO01", "phone": "13800000001"},
        )
        # half_open 下一次失败 -> open
        call_tool("agent", env, tenant_id=1, db=db)
        assert get_state("get_ticket_status")["state"] == STATE_OPEN