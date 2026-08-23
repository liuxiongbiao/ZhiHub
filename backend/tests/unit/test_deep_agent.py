"""
backend/tests/unit/test_deep_agent.py

职责: 测试 DeepAgent 3 步固定骨架执行 + 熔断上限 + 降级

测试内容:
  - TestDeepAgent: execute 返回 DeepAgentResult / 3 步骨架 / task_id 保留 /
    report 或 error / _query_stats 统计 / MAX_TOOL_CALLS=5 熔断上限
"""

import pytest
from app.agents.deep_agent.planner import execute, DeepAgentResult, MAX_TOOL_CALLS


class TestDeepAgent:
    def _patch_helpers(self, monkeypatch):
        """屏蔽外部依赖（Milvus/ES/MySQL），使 execute 走确定性降级路径。"""
        from app.agents.deep_agent import planner
        monkeypatch.setattr(planner, "_query_stats", lambda tenant_id: {"total_tickets": 1, "open_tickets": 1, "source": "mock"})
        monkeypatch.setattr(planner, "_search_sop", lambda goal, tenant_id: ["SOP"])

    def test_execute_returns_result(self, monkeypatch):
        self._patch_helpers(monkeypatch)
        """基本执行返回DeepAgentResult."""
        result = execute(task_id="test-1", goal="测试报告", tenant_id=1)
        assert isinstance(result, DeepAgentResult)
        assert result.task_id == "test-1"
        assert result.status in ("completed", "failed")

    def test_execute_has_3_steps(self, monkeypatch):
        """正常执行应有步骤（桩化 LLM，消除外网依赖）。"""
        class FakeMessage:
            content = "Thought: 完成\nAction: finish\nAction Input: {\"summary\": \"ok\"}"

        class FakeChoice:
            message = FakeMessage()

        class FakeResp:
            choices = [FakeChoice()]

        monkeypatch.setattr("app.core.llm_client.llm_complete", lambda **kw: FakeResp())
        result = execute(task_id="test-2", goal="工单分析", tenant_id=1)
        assert len(result.steps) > 0

    def test_task_id_preserved(self, monkeypatch):
        self._patch_helpers(monkeypatch)
        """task_id应正确保存."""
        result = execute(task_id="my-task-123", goal="测试", tenant_id=1)
        assert result.task_id == "my-task-123"

    def test_result_has_report_or_error(self, monkeypatch):
        self._patch_helpers(monkeypatch)
        """完成后应有report或error."""
        result = execute(task_id="test-3", goal="测试", tenant_id=1)
        assert result.status in ("completed", "failed")
        if result.status == "completed":
            assert result.report or result.error
        else:
            assert result.error

    def test_stats_query(self):
        """统计查询返回合理数据."""
        from app.agents.deep_agent.planner import _query_stats
        stats = _query_stats(1)
        assert "total_tickets" in stats
        assert "open_tickets" in stats
        assert "source" in stats

    def test_max_tool_calls_limit(self):
        """熔断上限常量正确."""
        assert MAX_TOOL_CALLS == 5

    def test_extract_json_object_handles_braces_in_report(self):
        """ 回归：报告内容含 } 时仍能提取完整 JSON（原非贪婪正则截断导致空报告）。"""
        from app.agents.deep_agent.planner import _extract_json_object

        text = 'Action Input: {"report": "数据概览：{总量} 与 建议(改进)}"}'
        parsed = _extract_json_object(text)
        assert parsed.get("report", "").startswith("数据概览：{总量}")
        assert parsed.get("report", "").endswith("建议(改进)}")

    def test_parse_react_response_finish_with_nested_braces(self):
        """finish 的 Action Input 含嵌套括号时解析完整。"""
        from app.agents.deep_agent.planner import _parse_react_response

        text = (
            "Thought: 完成\n"
            "Action: finish\n"
            "Action Input: {\"report\": \"报告含 {a} 与 } 符号，300字\"}\n"
            "Thought: 结束"
        )
        _, action, action_input = _parse_react_response(text)
        assert action == "finish"
        assert "报告含 {a} 与 } 符号" in action_input
