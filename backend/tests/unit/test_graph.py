"""
backend/tests/unit/test_graph.py

职责: 测试 LangGraph 图拓扑 + Router 条件边路由逻辑（纯逻辑，不调 LLM）
对应日程: 003-agent-graph

测试内容:
  - TestRouteIntent: 条件边路由（愤怒/敏感操作/低置信度 -> human_handoff；
    知识咨询/工单/订单查询正常路由）
  - TestRouterOutput: RouterOutput 结构 + fallback 降级默认值
  - TestGraphBuild: 图编译 + 必需节点（router/knowledge_agent/ticket_agent/order_agent/human_handoff）
"""

import pytest


class TestRouteIntent:
    """条件边路由逻辑测试."""

    def test_angry_routes_to_human_handoff(self):
        """情绪的愤怒 → 转人工."""
        from app.agents.graph import _route_intent

        state = {
            "intent": "knowledge_consult",
            "sentiment": "angry",
            "confidence": 0.9,
        }
        result = _route_intent(state)
        assert result == "human_handoff"

    def test_sensitive_action_routes_to_human_handoff(self):
        """敏感操作 → 转人工."""
        from app.agents.graph import _route_intent

        state = {
            "intent": "sensitive_action",
            "sentiment": "normal",
            "confidence": 0.9,
        }
        result = _route_intent(state)
        assert result == "human_handoff"

    def test_low_confidence_routes_to_human_handoff(self):
        """低置信度 → 转人工."""
        from app.agents.graph import _route_intent

        state = {
            "intent": "knowledge_consult",
            "sentiment": "normal",
            "confidence": 0.3,
        }
        result = _route_intent(state)
        assert result == "human_handoff"

    def test_knowledge_consult_routes_to_knowledge_agent(self):
        """正常知识咨询 → knowledge_agent."""
        from app.agents.graph import _route_intent

        state = {
            "intent": "knowledge_consult",
            "sentiment": "normal",
            "confidence": 0.85,
        }
        result = _route_intent(state)
        assert result == "knowledge_consult"

    def test_ticket_query_routes_normally(self):
        """工单查询正常路由."""
        from app.agents.graph import _route_intent

        state = {
            "intent": "ticket_query",
            "sentiment": "normal",
            "confidence": 0.88,
        }
        result = _route_intent(state)
        assert result == "ticket_query"

    def test_order_query_routes_normally(self):
        """订单查询正常路由."""
        from app.agents.graph import _route_intent

        state = {
            "intent": "order_query",
            "sentiment": "unhappy",
            "confidence": 0.75,
        }
        result = _route_intent(state)
        assert result == "order_query"


class TestRouterOutput:
    """Router 节点输出测试."""

    def test_router_output_model(self):
        """RouterOutput BaseModel 结构正确（011 PydanticAI）."""
        from app.agents.nodes.router import RouterOutput
        r = RouterOutput(intent="ticket_query", confidence=0.9, sentiment="normal", slots={"ticket_no": "TK-001"})
        assert r.intent == "ticket_query"
        assert r.confidence == 0.9

    def test_router_fallback_result(self):
        """fallback 降级返回默认值."""
        from app.agents.nodes.router import _fallback_result
        result = _fallback_result()
        assert result["intent"] == "knowledge_consult"
        assert result["confidence"] == 0.3

class TestGraphBuild:
    """图编译测试."""

    def test_graph_builds_successfully(self):
        """验证图能正常编译."""
        from app.agents.graph import build_graph

        graph = build_graph()
        assert graph is not None

    def test_graph_has_required_nodes(self):
        """验证图包含全部必需节点."""
        from app.agents.graph import build_graph

        graph = build_graph()
        nodes = list(graph.nodes.keys())
        assert "router" in nodes
        assert "knowledge_agent" in nodes
        assert "ticket_agent" in nodes
        assert "order_agent" in nodes
        assert "human_handoff" in nodes

    def test_graph_routing_decision(self, monkeypatch):
        """知识咨询意图 -> 图完整走通（T34/M8: mock LLM 节点，原真调 LLM 依赖 key）。"""
        from app.agents.graph import build_graph

        # 桩掉 LLM 节点: patch 目标必须是 graph 模块的绑定引用——
        # graph.py 用 `from ... import router_node` 在模块加载时绑定，patch 源模块
        # (app.agents.nodes.router.router_node) 不影响 graph 已绑定的引用（原实现是假阳性，
        # 实际跑真实 router 靠 human_handoff 路径巧合通过且耗时 ~48s）。
        # patch 在 build_graph 前执行，编译时 add_node 读到的是 fake 函数。
        def _fake_router(state):
            return {
                **state, "intent": "knowledge_consult", "confidence": 0.9,
                "sentiment": "normal", "slots": {},
            }
        monkeypatch.setattr("app.agents.graph.router_node", _fake_router)

        def _fake_ka(state):
            return {**state, "final_response": "知识回复", "retrieval_result": []}
        monkeypatch.setattr("app.agents.graph.knowledge_agent_node", _fake_ka)

        graph = build_graph()

        state = {
            "session_id": 1,
            "tenant_id": 1,
            "messages": [{"role": "user", "content": "如何升级套餐？"}],
            "intent": "",
            "confidence": 0.0,
            "sentiment": "normal",
            "slots": {},
            "retrieval_result": [],
            "current_agent": "router",
            "should_interrupt": False,
            "final_response": "",
        }

        # 图完整执行（LLM 已桩）: router 路由到 knowledge_agent -> final_response
        result = graph.invoke(state)
        assert "final_response" in result
        assert result.get("intent") == "knowledge_consult"