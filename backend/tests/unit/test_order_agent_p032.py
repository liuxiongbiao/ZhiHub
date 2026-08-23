"""单元/契约测试： 订单查询手机号后四位支持。

order agent 应接受完整 11 位手机号或 4 位后缀；MCP 层双因子保持
"订单号 + 手机号后 4 位"校验语义不变。
"""

import pytest


def _run_agent(monkeypatch, user_text, slots=None):
    """驱动 order_agent_node，捕获传给 MCP 的参数，返回 (result, captured_params)。"""
    from app.agents.nodes import order_agent

    captured = {}

    def fake_call_tool(role, env, tenant_id=None):
        captured.update(env["params"])
        return {
            "result": {
                "order_id": 1, "order_no": "ORD-DEMO-0003",
                "amount": 899.0, "paid_amount": 899.0,
                "status": "shipped", "tracking_no": "SF1234567890",
            }
        }

    from app.services import mcp_service
    monkeypatch.setattr(mcp_service, "call_tool", fake_call_tool)
    state = {
        "messages": [{"role": "user", "content": user_text}],
        "slots": dict(slots or {}),
        "tenant_id": 1,
    }
    return order_agent.order_agent_node(state), captured


class TestOrderAgentPhoneSuffix:
    def test_accepts_last4_suffix_without_asking_full_phone(self, monkeypatch):
        """用户提供订单号 + 后四位 2222：一轮直接调工具并返回金额/状态/物流单号。"""
        result, params = _run_agent(
            monkeypatch, "帮我查一下订单 ORD-DEMO-0003，我的手机号后四位是 2222"
        )
        assert params["order_no"] == "ORD-DEMO-0003"
        assert params["phone"] == "2222"
        assert "899" in result["final_response"]
        assert "已发货" in result["final_response"]
        assert "SF1234567890" in result["final_response"]

    def test_accepts_full_11digit_phone(self, monkeypatch):
        """完整 11 位手机号仍然可用（后 4 位参与双因子）。"""
        result, params = _run_agent(
            monkeypatch, "帮我查一下订单 ORD-DEMO-0003，手机号 13911112222"
        )
        assert params["phone"] == "13911112222"
        assert "899" in result["final_response"]

    def test_slots_phone_suffix_alone_still_works(self, monkeypatch):
        """slots 中只有 4 位后缀（如上游已抽取）也直接使用。"""
        result, params = _run_agent(
            monkeypatch, "查订单 ORD-DEMO-0003", slots={"phone": "2222"}
        )
        assert params["phone"] == "2222"

    def test_missing_phone_asks_for_suffix_or_full(self, monkeypatch):
        """缺手机号时反问，且话术允许提供后四位或完整号码。"""
        result, _params = _run_agent(monkeypatch, "帮我查一下订单 ORD-DEMO-0003")
        assert "2222" not in result["final_response"]
        assert "手机号" in result["final_response"]
        assert ("后四位" in result["final_response"]) or ("后 4 位" in result["final_response"])

    def test_invalid_phone_length_still_asks(self, monkeypatch):
        """长度非法（既非 11 位也非 4 位）的手机号必须反问，不调工具。"""
        result, params = _run_agent(
            monkeypatch, "查订单 ORD-DEMO-0003，手机号 12345"
        )
        assert params == {}
        assert "手机号" in result["final_response"]


class TestMcpQueryOrderPhoneSuffix:
    """MCP 层：4 位后缀与完整号码等价（后 4 位双因子）。"""

    @pytest.fixture()
    def seeded_order(self, db):
        from app.models.order import Order

        db.add(Order(
            tenant_id=1, order_no="ORD-P032-0001", amount=899.0,
            paid_amount=899.0, status="shipped", phone="13911112222",
            tracking_no="SF1234567890", version=1,
        ))
        db.commit()
        return "ORD-P032-0001"

    def test_query_with_last4_suffix_succeeds(self, db, seeded_order):
        from app.mcp_servers.order_server import handle_order_tool

        result = handle_order_tool(
            "query_order",
            {"order_no": seeded_order, "phone": "2222", "tenant_id": 1},
            db=db,
        )
        assert result["order_no"] == seeded_order
        assert result["tracking_no"] == "SF1234567890"

    def test_query_with_full_phone_succeeds(self, db, seeded_order):
        from app.mcp_servers.order_server import handle_order_tool

        result = handle_order_tool(
            "query_order",
            {"order_no": seeded_order, "phone": "13911112222", "tenant_id": 1},
            db=db,
        )
        assert result["order_no"] == seeded_order

    def test_query_with_wrong_suffix_rejected(self, db, seeded_order):
        from app.mcp_servers.base_server import IdentityVerificationError
        from app.mcp_servers.order_server import handle_order_tool

        with pytest.raises(IdentityVerificationError):
            handle_order_tool(
                "query_order",
                {"order_no": seeded_order, "phone": "9999", "tenant_id": 1},
                db=db,
            )
