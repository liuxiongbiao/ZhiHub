"""常见知识问题回答缓存的安全边界与流式命中测试。"""

from app.models.session import Session


def test_cache_eligibility_rejects_context_identifiers_and_sensitive_actions():
    """仅无历史、无业务编号、无敏感操作的通用问题允许共享缓存。"""
    from app.services.agent_service import _is_answer_cache_eligible

    assert _is_answer_cache_eligible("登录页面一直转圈怎么办？", [])
    assert not _is_answer_cache_eligible(
        "登录页面一直转圈怎么办？",
        [{"role": "assistant", "content": "上文"}],
    )
    assert not _is_answer_cache_eligible("查询订单 202608230001", [])
    assert not _is_answer_cache_eligible("我要退款", [])


def test_cache_key_normalizes_punctuation_and_does_not_expose_plaintext():
    """等价标点映射到同一哈希键，键中不出现用户原文。"""
    from app.services.agent_service import _answer_cache_key

    first = _answer_cache_key(1, "登录页面一直转圈怎么办？")
    second = _answer_cache_key(1, "登录页面一直转圈怎么办")
    assert first == second
    assert "登录" not in first


def test_prepare_stream_uses_cached_common_answer(db, monkeypatch):
    """缓存命中后不进入图或外部模型，仍返回引用和标准预处理结构。"""
    from app.services import agent_service, billing_service

    session = Session(tenant_id=1, status="created", end_user_id=None)
    db.add(session)
    db.commit()
    db.refresh(session)
    monkeypatch.setattr(billing_service, "is_degraded", lambda tenant_id: False)
    monkeypatch.setattr(agent_service, "_detect_injection", lambda *args, **kwargs: None)

    question = "登录页面一直转圈怎么办？"
    agent_service._store_cached_answer(
        1,
        question,
        {
            "content": "请清理缓存后重新登录。",
            "sources": [{"doc": "常见故障排查.md", "chunk_index": 1, "text": "清理缓存"}],
            "intent": "knowledge_consult",
            "confidence": 0.96,
        },
    )
    result = agent_service.prepare_message_stream(
        db,
        session_id=session.id,
        tenant_id=1,
        user_message=question,
    )

    assert result["need_stream"] is False
    assert result["content"] == "请清理缓存后重新登录。"
    assert result["final_state"]["cache_hit"] is True
    assert result["retrieval_result"][0]["doc_filename"] == "常见故障排查.md"


def test_memory_save_runs_in_background(monkeypatch):
    """长期记忆抽取通过后台线程启动，不阻塞回答收尾。"""
    from app.services import agent_service, memory_service

    calls: list[tuple] = []

    class ImmediateThread:
        """测试用线程桩：start 时同步执行目标，便于确定性断言。"""

        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    monkeypatch.setattr(agent_service.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(agent_service.settings, "llm_enabled", True)
    monkeypatch.setattr(memory_service, "extract_facts", lambda text: [{"fact_type": "plan", "content": text}])
    monkeypatch.setattr(
        memory_service,
        "save_memory",
        lambda tenant_id, end_user_id, facts: calls.append((tenant_id, end_user_id, facts)),
    )

    agent_service._save_memory_non_blocking(1, "eu-1", "企业版")
    assert calls == [(1, "eu-1", [{"fact_type": "plan", "content": "企业版"}])]
