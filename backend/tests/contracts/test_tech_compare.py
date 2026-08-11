"""
backend/tests/contracts/test_tech_compare.py

职责: 测试 009 技术栈对比与智能体增强的契约（FC 对比 / 长期记忆 / Agent 评测 helpers）
对应日程: 009-tech-compare-agent-enhance

测试内容:
  - TestFunctionCallingAgent: FC 版工单 Agent（tools schema 结构 + 缺 phone 反问 +
    LLM 返回 tool_calls -> 复用 mcp_service.call_tool + 无 tool_call 兜底）
  - TestMemoryService: 长期记忆（LLM 抽取事实 -> 结构化列表 + 空输入/降级返回空 +
    save/search 失败降级不抛）
  - TestAgentEval: Agent 评测脚本 helper（TEST_SET 结构 + 准确率计算 + 报告格式）
"""


# ═══════════════════════════════════════════════════════════
# T003: Function Calling 对比（User Story 1）
# SC-001: FC 模式查工单结果与 MCP 一致
# ═══════════════════════════════════════════════════════════
class TestFunctionCallingAgent:
    """FC 版工单 Agent 契约。"""

    def test_fc_tools_schema(self):
        """FC tools schema 结构正确。"""
        from app.agents.nodes.ticket_agent_fc import _build_tools
        tools = _build_tools()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "get_ticket_status"
        params = tools[0]["function"]["parameters"]
        assert "ticket_no" in params["properties"]
        assert "phone" in params["properties"]
        assert set(params["required"]) == {"ticket_no", "phone"}

    def test_fc_phone_missing_asks(self):
        """FC 模式缺 phone -> 反问手机号（与 MCP 一致）。"""
        from app.agents.nodes.ticket_agent_fc import ticket_agent_fc_node
        state = {
            "messages": [{"role": "user", "content": "帮我查工单 TK-20240001"}],
            "slots": {"ticket_no": "TK-20240001"},
            "tenant_id": 1,
        }
        result = ticket_agent_fc_node(state)
        assert "手机号" in result["final_response"]
        assert result["current_agent"] == "ticket_agent_fc"

    def test_fc_dispatches_via_tool_call(self, monkeypatch):
        """FC 模式: LLM 返回 tool_calls -> 调 mcp_service.call_tool（复用 006）。"""
        from app.agents.nodes import ticket_agent_fc

        # mock LLM 返回 tool_call
        class MockMsg:
            tool_calls = [{"id": "1", "function": {"name": "get_ticket_status", "arguments": "{}"}}]
        class MockResp:
            choices = [type("c", (), {"message": MockMsg()})()]
        def mock_completion(**kwargs):
            return MockResp()
        monkeypatch.setattr("litellm.completion", mock_completion)

        # mock call_tool 返回成功
        def mock_call_tool(role, envelope, *, tenant_id=None, db=None):
            return {"jsonrpc": "2.0", "id": "x", "result": {
                "ticket_no": "TK-20240001", "status": "open", "title": "测试", "priority": "high",
            }}
        monkeypatch.setattr("app.services.mcp_service.call_tool", mock_call_tool)

        state = {
            "messages": [{"role": "user", "content": "查工单 TK-20240001，手机号 13800000001"}],
            "slots": {"ticket_no": "TK-20240001", "phone": "13800000001"},
            "tenant_id": 1,
        }
        result = ticket_agent_fc.ticket_agent_fc_node(state)
        assert "TK-20240001" in result["final_response"]
        assert "待处理" in result["final_response"]  # status open -> 待处理

    def test_fc_no_tool_call_fallback(self, monkeypatch):
        """FC 模式: LLM 不返回 tool_call -> 兜底回复。"""
        from app.agents.nodes import ticket_agent_fc

        class MockMsg:
            tool_calls = None
        class MockResp:
            choices = [type("c", (), {"message": MockMsg()})()]
        monkeypatch.setattr("litellm.completion", lambda **kw: MockResp())

        state = {
            "messages": [{"role": "user", "content": "查工单 TK-20240001，手机号 13800000001"}],
            "slots": {"ticket_no": "TK-20240001", "phone": "13800000001"},
            "tenant_id": 1,
        }
        result = ticket_agent_fc.ticket_agent_fc_node(state)
        assert "问题" in result["final_response"]  # 兜底


# ═══════════════════════════════════════════════════════════
# T008: 长期记忆（User Story 2）
# SC-003: 会话 A 偏好 -> 会话 B 召回体现
# ═══════════════════════════════════════════════════════════
class TestMemoryService:
    """memory_service 长期记忆契约。"""

    def test_extract_facts_with_mock_llm(self, monkeypatch):
        """LLM 抽取关键事实 -> 结构化列表。"""
        from app.services import memory_service

        class MockResp:
            choices = [type("c", (), {"message": type("m", (), {"content": '[{"fact_type":"plan","content":"企业版套餐"}]'})()})()]
        monkeypatch.setattr("litellm.completion", lambda **kw: MockResp())

        facts = memory_service.extract_facts("我是企业版套餐用户")
        assert len(facts) == 1
        assert facts[0]["fact_type"] == "plan"
        assert "企业版" in facts[0]["content"]

    def test_extract_facts_empty(self, monkeypatch):
        """无关键事实 -> 空列表。"""
        from app.services import memory_service

        class MockResp:
            choices = [type("c", (), {"message": type("m", (), {"content": "[]"})()})()]
        monkeypatch.setattr("litellm.completion", lambda **kw: MockResp())

        assert memory_service.extract_facts("今天天气不错") == []

    def test_extract_facts_none_input(self):
        from app.services import memory_service
        assert memory_service.extract_facts("") == []
        assert memory_service.extract_facts(None) == []  # type: ignore

    def test_save_memory_degradation(self, monkeypatch):
        """save_memory 失败 -> 降级不抛。"""
        from app.services import memory_service

        # mock init 抛异常
        monkeypatch.setattr("app.core.embedding_client.init_memory_collection", lambda: (_ for _ in ()).throw(Exception("no milvus")))
        # 不应抛异常
        memory_service.save_memory(1, 1, [{"fact_type": "plan", "content": "企业版"}])

    def test_search_memory_degradation(self, monkeypatch):
        """search_memory 失败 -> 降级返回空。"""
        from app.services import memory_service

        monkeypatch.setattr("app.core.embedding_client.init_memory_collection", lambda: (_ for _ in ()).throw(Exception("no milvus")))
        result = memory_service.search_memory(1, 1, "套餐")
        assert result == []

    def test_search_memory_empty_query(self):
        from app.services import memory_service
        assert memory_service.search_memory(1, 1, "") == []


# ═══════════════════════════════════════════════════════════
# T012: Agent 评测 helpers（User Story 3）
# SC-004: 评测产出意图准确率 + 工具调用成功率
# ═══════════════════════════════════════════════════════════
class TestAgentEval:
    """Agent 评测脚本 helper 契约（无 Docker/LLM 依赖）。"""

    def test_test_set_structure(self):
        """测试集结构正确（~10 条，含 expected_intent + expected_tool）。"""
        from scripts.agent_eval import TEST_SET
        assert len(TEST_SET) >= 10
        for item in TEST_SET:
            assert "message" in item
            assert "expected_intent" in item
            assert "expected_tool" in item

    def test_calculate_accuracy(self):
        """准确率计算正确。"""
        from scripts.agent_eval import calculate_accuracy
        results = [
            {"intent_ok": True, "tool_ok": True},
            {"intent_ok": True, "tool_ok": False},
            {"intent_ok": False, "tool_ok": True},
            {"intent_ok": True, "tool_ok": True},
        ]
        acc = calculate_accuracy(results)
        assert acc["intent_accuracy"] == 0.75
        assert acc["tool_accuracy"] == 0.75
        assert acc["total"] == 4

    def test_format_report(self):
        """报告格式含意图准确率 + 工具成功率。"""
        from scripts.agent_eval import format_report
        results = [{"q": 1, "message": "test", "expected_intent": "x", "actual_intent": "x",
                     "expected_tool": "t", "actual_tool": "t", "intent_ok": True, "tool_ok": True}]
        acc = {"intent_accuracy": 1.0, "tool_accuracy": 1.0, "total": 1}
        report = format_report(results, acc)
        assert "意图识别准确率" in report
        assert "工具调用成功率" in report
        assert "100.0%" in report