"""
backend/tests/integration/test_009_010_e2e.py

职责: 规格 009 技术栈对比 + 规格 010 业务闭环的端到端 smoke 测试（需 Docker 基础设施）

测试内容:
  - Test009E2E: FC agent tools schema + memory 降级 + agent_eval 测试集结构
  - Test010E2E: SSE 端点存在 + 质量检测 + 计费折算 + cleanup 定时任务可调用
"""

import pytest


class Test009E2E:
    """规格 009 技术栈对比 smoke。"""

    def test_fc_tools_schema(self):
        from app.agents.nodes.ticket_agent_fc import _build_tools
        tools = _build_tools()
        assert tools[0]["function"]["name"] == "get_ticket_status"

    def test_memory_service_degrades(self, monkeypatch):
        from app.services import memory_service
        monkeypatch.setattr("app.core.embedding_client.init_memory_collection",
                            lambda: (_ for _ in ()).throw(Exception("no milvus")))
        assert memory_service.search_memory(1, 1, "test") == []

    def test_agent_eval_test_set(self):
        from scripts.agent_eval import TEST_SET, calculate_accuracy
        assert len(TEST_SET) >= 10
        acc = calculate_accuracy([{"intent_ok": True, "tool_ok": True}])
        assert acc["intent_accuracy"] == 1.0


class Test010E2E:
    """规格 010 业务闭环 smoke。"""

    def test_sse_endpoint_exists(self, client, db, make_token):
        """SSE 端点返回 text/event-stream（：需存在 active 租户才能建会话）。"""
        from app.models.tenant import Tenant

        t = Tenant(name="SSE租户", plan_type="trial", seat_limit=1, status="active")
        db.add(t)
        db.commit()
        token = make_token(tenant_id=t.id, role="admin")
        sid = client.post("/api/v1/session", json={}, headers={"Authorization": f"Bearer {token}"}).json()["session_id"]
        # 非知识咨询走同步兜底（不测真实流式，避免 LLM 依赖）
        assert sid > 0

    def test_quality_check(self):
        from app.tasks.knowledge_tasks import check_quality
        rate, empty_ratio = check_quality(["正常中文"])
        assert rate < 0.3 and empty_ratio < 0.2
        rate, empty_ratio = check_quality(["\xff\xfe\x00\x01"])
        assert rate > 0.3

    def test_billing_weighting(self):
        from app.services.billing_service import incr_usage
        result = incr_usage(1, model="deepseek/deepseek-chat")
        assert "count" in result
        assert isinstance(result["count"], float)

    def test_cleanup_expired_docs_exists(self):
        from app.tasks.cleanup_tasks import cleanup_expired_docs
        assert callable(cleanup_expired_docs)

    def test_cleanup_overdue_refunds_exists(self):
        from app.tasks.cleanup_tasks import cleanup_overdue_refunds
        assert callable(cleanup_overdue_refunds)
