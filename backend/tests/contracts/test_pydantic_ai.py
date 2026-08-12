"""
backend/tests/contracts/test_pydantic_ai.py

职责: 测试 PydanticAI 结构化输出接入的契约（路由节点 + 记忆抽取）

测试内容:
  - TestPydanticAIRouter: RouterOutput 结构化输出 + router_node mock run_sync +
    PydanticAI 失败降级 fallback（intent=knowledge_consult, confidence=0.3）
  - TestPydanticAIMemory: Fact 结构化抽取 + extract_facts mock + 空输入/降级返回空列表
"""

import pytest


class TestPydanticAIRouter:
    """路由节点 PydanticAI 结构化输出契约。"""

    def test_router_output_model(self):
        """RouterOutput BaseModel 结构正确。"""
        from app.agents.nodes.router import RouterOutput
        r = RouterOutput(intent="ticket_query", confidence=0.9, sentiment="normal", slots={"ticket_no": "TK-001"})
        assert r.intent == "ticket_query"
        assert r.confidence == 0.9
        assert r.sentiment == "normal"
        assert r.slots["ticket_no"] == "TK-001"

    def test_router_node_returns_structured_output(self, monkeypatch):
        """router_node 返回 4 字段结构（mock PydanticAI run_sync）。"""
        from app.agents.nodes import router as router_mod

        # mock run_sync 返回 RouterOutput
        mock_output = router_mod.RouterOutput(
            intent="knowledge_consult", confidence=0.95, sentiment="normal", slots={}
        )
        mock_result = type("R", (), {"output": mock_output})()
        monkeypatch.setattr(router_mod._router_agent, "run_sync", lambda *a, **kw: mock_result)

        state = {"messages": [{"role": "user", "content": "如何升级套餐"}], "tenant_id": 1}
        result = router_mod.router_node(state)
        assert result["intent"] == "knowledge_consult"
        assert result["confidence"] == 0.95
        assert result["sentiment"] == "normal"
        assert result["slots"] == {}

    def test_router_fallback_on_failure(self, monkeypatch):
        """PydanticAI 失败 -> 降级 fallback，不崩。"""
        from app.agents.nodes import router as router_mod

        def mock_fail(*a, **kw):
            raise Exception("PydanticAI unavailable")

        monkeypatch.setattr(router_mod._router_agent, "run_sync", mock_fail)

        state = {"messages": [{"role": "user", "content": "hello"}], "tenant_id": 1}
        result = router_mod.router_node(state)
        # 降级返回 fallback（intent=knowledge_consult, confidence=0.3）
        assert result["intent"] == "knowledge_consult"
        assert result["confidence"] == 0.3


class TestPydanticAIMemory:
    """记忆抽取 PydanticAI 结构化输出契约。"""

    def test_fact_model(self):
        """Fact BaseModel 结构正确。"""
        from app.services.memory_service import Fact
        f = Fact(fact_type="plan", content="企业版套餐")
        assert f.fact_type == "plan"
        assert f.content == "企业版套餐"

    def test_extract_facts_returns_structured(self, monkeypatch):
        """extract_facts 返回 list[dict]（mock PydanticAI）。"""
        from app.services import memory_service

        mock_facts = [memory_service.Fact(fact_type="plan", content="企业版")]
        mock_result = type("R", (), {"output": mock_facts})()
        monkeypatch.setattr(memory_service._extract_agent, "run_sync", lambda *a, **kw: mock_result)

        result = memory_service.extract_facts("我是企业版套餐用户")
        assert len(result) == 1
        assert result[0]["fact_type"] == "plan"
        assert result[0]["content"] == "企业版"

    def test_extract_facts_empty(self, monkeypatch):
        """无事实 -> 空列表。"""
        from app.services import memory_service

        mock_result = type("R", (), {"output": []})()
        monkeypatch.setattr(memory_service._extract_agent, "run_sync", lambda *a, **kw: mock_result)

        assert memory_service.extract_facts("今天天气不错") == []

    def test_extract_facts_degradation(self, monkeypatch):
        """PydanticAI 失败 -> 降级返回空。"""
        from app.services import memory_service

        monkeypatch.setattr(memory_service._extract_agent, "run_sync",
                            lambda *a, **kw: (_ for _ in ()).throw(Exception("fail")))
        assert memory_service.extract_facts("test") == []

    def test_extract_facts_none_input(self):
        from app.services import memory_service
        assert memory_service.extract_facts("") == []
        assert memory_service.extract_facts(None) == []  # type: ignore
