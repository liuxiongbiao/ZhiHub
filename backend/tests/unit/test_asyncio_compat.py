"""
backend/tests/unit/test_asyncio_compat.py

职责: 修复 PydanticAI run_sync 在事件循环线程中的降级问题
      （async 端点 /session/message/stream → graph.invoke → run_sync 内部 asyncio.run
        抛 "This event loop is already running" → Router/记忆抽取持续降级）。

对应: 013 UI 联调发现的 P0 问题（connectivity-api.err.log 实证）。

测试内容:
  - run_sync_in_thread: 事件循环中执行内部 asyncio.run 的函数不抛错
  - router_node: async 上下文中不降级（confidence 保留 0.9 而非 fallback 0.3）
  - extract_facts: async 上下文中不降级（返回 facts 而非 []）
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.utils.asyncio_compat import run_sync_in_thread


def _make_run_sync_like(value):
    """模拟 PydanticAI run_sync：内部使用 asyncio.run（事件循环中会抛错）。"""

    def _run_sync(_prompt):
        async def _inner():
            return value

        return asyncio.run(_inner())

    return _run_sync


@pytest.mark.asyncio
async def test_run_sync_in_thread_works_inside_event_loop():
    """事件循环中调用内部 asyncio.run 的函数 → 由 helper 转到独立线程执行，不抛错。"""
    fn = _make_run_sync_like("ok")
    result = run_sync_in_thread(fn, "prompt")
    assert result == "ok"


def test_run_sync_in_thread_direct_when_no_loop():
    """无事件循环（同步上下文）→ 直接执行。"""
    fn = _make_run_sync_like(42)
    assert run_sync_in_thread(fn, "prompt") == 42


@pytest.mark.asyncio
async def test_router_node_keeps_confidence_inside_event_loop(monkeypatch):
    """async 上下文调用 router_node → Router 不降级（confidence=0.9 而非 fallback 0.3）。"""
    from app.agents.nodes import router

    fake_output = router.RouterOutput(
        intent="knowledge_consult", confidence=0.9, sentiment="normal", slots={}
    )
    monkeypatch.setattr(
        router._router_agent, "run_sync", _make_run_sync_like(SimpleNamespace(output=fake_output))
    )
    state = {"messages": [{"role": "user", "content": "你好"}]}
    result = router.router_node(state)
    assert result["intent"] == "knowledge_consult"
    assert result["confidence"] == 0.9
    assert result["confidence"] != router.FALLBACK_CONFIDENCE


@pytest.mark.asyncio
async def test_extract_facts_keeps_facts_inside_event_loop(monkeypatch):
    """async 上下文调用 extract_facts → 不降级（返回抽取的 facts 而非 []）。"""
    from app.services import memory_service

    fake_facts = [SimpleNamespace(fact_type="plan", content="企业客户")]

    class FakeExtractAgent:
        def run_sync(self, user_message):
            async def _inner():
                return SimpleNamespace(output=fake_facts)

            return asyncio.run(_inner())

    monkeypatch.setattr(memory_service, "_extract_agent", FakeExtractAgent())
    facts = memory_service.extract_facts("我们企业客户主要使用智枢系统")
    assert facts == [{"fact_type": "plan", "content": "企业客户"}]
