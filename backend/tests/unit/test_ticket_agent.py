"""
backend/tests/unit/test_ticket_agent.py

职责: 测试工单/订单 Agent 的槽位补全与号码提取
对应日程: 003-agent-graph

测试内容:
  - TestTicketSlotFilling: 无工单号反问格式 / slots 含工单号跳过反问 / 从消息正则提取 TK- 号
  - TestOrderSlotFilling: 从消息正则提取 ORD- 号 / 无订单号反问
"""


class TestTicketSlotFilling:
    def test_missing_ticket_no_asks_clarification(self):
        """无工单号时应反问."""
        from app.agents.nodes.ticket_agent import ticket_agent_node

        state = {
            "messages": [{"role": "user", "content": "帮我查一下工单进度"}],
            "slots": {},
            "tenant_id": 1,
        }
        result = ticket_agent_node(state)
        assert "final_response" in result
        assert "TK-" in result["final_response"]  # 提醒格式

    def test_has_ticket_no_in_slots(self):
        """slots含工单号时跳过反问。注意: 实际调用需要DB/MCP，此处测逻辑分支."""
        from app.agents.nodes.ticket_agent import ticket_agent_node

        state = {
            "messages": [{"role": "user", "content": "查工单进度"}],
            "slots": {"ticket_no": "TK-20240001"},
            "tenant_id": 1,
        }
        result = ticket_agent_node(state)
        # 尝试调MCP → 无DB时返回错误提示
        assert "final_response" in result

    def test_extract_ticket_no_from_message(self):
        """从用户消息中提取工单号: 调 ticket_agent_node 验证槽位提取."""
        from app.agents.nodes.ticket_agent import ticket_agent_node

        state = {
            "messages": [{"role": "user", "content": "工单TK-20240001处理得怎么样了？"}],
            "slots": {},
            "tenant_id": 1,
        }
        result = ticket_agent_node(state)
        # ticket_no 被提取到 slots 中（无 phone 时会反问手机号，而非反问工单号）
        assert result["slots"]["ticket_no"] == "TK-20240001"
        # 因缺 phone 反问手机号，而非反问工单号 -> 证明 ticket_no 已成功提取
        assert "手机号" in result["final_response"]


class TestOrderSlotFilling:
    def test_extract_order_no_from_message(self):
        """从用户消息中提取订单号: 调 order_agent_node 验证槽位提取."""
        from app.agents.nodes.order_agent import order_agent_node

        state = {
            "messages": [{"role": "user", "content": "订单ORD-20240001发货了吗？"}],
            "slots": {},
            "tenant_id": 1,
        }
        result = order_agent_node(state)
        # order_no 被提取到 slots 中（无 phone 时会反问手机号，而非反问订单号）
        assert result["slots"]["order_no"] == "ORD-20240001"
        # 因缺 phone 反问手机号，而非反问订单号 -> 证明 order_no 已成功提取
        assert "手机号" in result["final_response"]

    def test_missing_order_no_asks(self):
        """无订单号时应反问."""
        from app.agents.nodes.order_agent import order_agent_node

        state = {
            "messages": [{"role": "user", "content": "我的订单怎么样了"}],
            "slots": {},
            "tenant_id": 1,
        }
        result = order_agent_node(state)
        assert "ORD-" in result["final_response"]
