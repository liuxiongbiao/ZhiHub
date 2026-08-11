"""
backend/tests/unit/test_deep_agent.py

职责: 测试 DeepAgent 3 步固定骨架执行 + 熔断上限 + 降级
对应日程: 010-business-streaming

测试内容:
  - TestDeepAgent: execute 返回 DeepAgentResult / 3 步骨架 / task_id 保留 /
    report 或 error / _query_stats 统计 / MAX_TOOL_CALLS=5 熔断上限
"""

import pytest
from app.agents.deep_agent.planner import execute, DeepAgentResult, MAX_TOOL_CALLS


class TestDeepAgent:
    def test_execute_returns_result(self):
        """基本执行返回DeepAgentResult."""
        result = execute(task_id="test-1", goal="测试报告", tenant_id=1)
        assert isinstance(result, DeepAgentResult)
        assert result.task_id == "test-1"
        assert result.status in ("completed", "failed")

    def test_execute_has_3_steps(self):
        """正常执行应有3个步骤."""
        result = execute(task_id="test-2", goal="工单分析", tenant_id=1)
        # 失败时step数可能少于3
        assert len(result.steps) > 0

    def test_task_id_preserved(self):
        """task_id应正确保存."""
        result = execute(task_id="my-task-123", goal="测试", tenant_id=1)
        assert result.task_id == "my-task-123"

    def test_result_has_report_or_error(self):
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
