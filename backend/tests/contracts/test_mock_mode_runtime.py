"""本机 mock/off 模式必须走显式降级，不能先触发外部模型再靠异常兜底。"""

import sys
import types


def test_llm_disabled_skips_graph_and_memory_and_marks_degraded(db, monkeypatch):
    from app.config import settings
    from app.models.session import Session
    from app.models.tenant import Tenant
    from app.services import agent_service, memory_service, trace_service

    tenant = Tenant(name="LLM关闭租户", plan_type="trial", seat_limit=1, status="active")
    db.add(tenant)
    db.commit()
    session = Session(
        tenant_id=tenant.id,
        status="agent_handling",
        end_user_id="mock-mode-user",
    )
    db.add(session)
    db.commit()

    graph_calls = {"count": 0}

    def _forbidden_graph():
        graph_calls["count"] += 1
        raise AssertionError("LLM-disabled mode must not construct the graph")

    monkeypatch.setitem(
        sys.modules,
        "app.agents.graph",
        types.SimpleNamespace(get_graph=_forbidden_graph),
    )
    monkeypatch.setattr(
        memory_service,
        "search_memory",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("memory LLM must stay disabled")),
    )
    monkeypatch.setattr(
        memory_service,
        "extract_facts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("memory LLM must stay disabled")),
    )

    async def _noop_trace(*args, **kwargs):
        return None

    monkeypatch.setattr(trace_service, "save_trace", _noop_trace)
    monkeypatch.setattr(agent_service, "_notify_new_session", lambda **kwargs: None)
    monkeypatch.setattr(settings, "llm_enabled", False)

    result = agent_service.handle_message(
        db,
        session_id=session.id,
        tenant_id=tenant.id,
        user_message="如何申请退款？",
    )

    db.refresh(session)
    assert graph_calls["count"] == 0
    assert result["metadata"]["degraded"] is True
    assert "人工客服" in result["content"]
    assert session.status == "waiting_human"
