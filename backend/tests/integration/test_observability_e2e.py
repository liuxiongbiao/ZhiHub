"""
backend/tests/integration/test_observability_e2e.py

职责: 008 可观测性与效果评测的端到端集成测试（脱敏集成 + Langfuse 降级）

测试内容:
  - TestTraceDesensitization: trace_service._build_steps 集成 mask_pii
    （slots 中 phone 脱敏，编号不误伤；answer_preview 脱敏；无 phone 不变）
  - TestLangfuseDegradationE2E: 未配置 key -> @observe no-op，主流程不崩

注: Langfuse cloud 真实 trace 上报依赖网络+密钥，不适合自动化测试，由 T010 手动验收。
"""

import pytest


class TestTraceDesensitization:
    """trace_service 集成 mask_pii 的端到端脱敏（US3）。"""

    def test_build_steps_masks_phone_in_query_and_answer(self):
        """含手机号的 state -> steps 中 query/answer_preview/slots 手机号脱敏。"""
        from app.services.trace_service import _build_steps

        state = {
            "intent": "ticket_query",
            "confidence": 0.9,
            "sentiment": "normal",
            "slots": {"ticket_no": "TK-20240001", "phone": "13800000001"},
            "messages": [{"role": "user", "content": "查工单 TK-20240001 手机号13800000001"}],
            "retrieval_result": [],
            "current_agent": "ticket_agent",
            "final_response": "工单 TK-20240001 状态 open，绑定手机13800000001",
        }
        steps = _build_steps(state, total_elapsed_ms=120)

        # router step: slots 中 phone 脱敏
        router_step = next(s for s in steps if s["step"] == "router")
        assert router_step["slots"]["phone"] == "138****0001"
        assert router_step["slots"]["ticket_no"] == "TK-20240001"  # 编号不脱敏

        # generation step: answer_preview 脱敏
        gen_step = next(s for s in steps if s["step"] == "generation")
        assert "13800000001" not in gen_step["answer_preview"]
        assert "138****0001" in gen_step["answer_preview"]

    def test_build_steps_no_phone_unchanged(self):
        """无手机号的 state -> steps 不受影响。"""
        from app.services.trace_service import _build_steps

        state = {
            "intent": "knowledge_consult",
            "confidence": 0.95,
            "slots": {},
            "messages": [{"role": "user", "content": "如何升级套餐"}],
            "retrieval_result": [{"doc_filename": "manual.md", "chunk_text": "...", "score": 0.9}],
            "current_agent": "knowledge_agent",
            "final_response": "可在后台设置中升级套餐",
        }
        steps = _build_steps(state, total_elapsed_ms=100)
        gen_step = next(s for s in steps if s["step"] == "generation")
        assert gen_step["answer_preview"] == "可在后台设置中升级套餐"


class TestLangfuseDegradationE2E:
    """Langfuse 降级端到端（US2 SC-005）：未配置 key 时 @observe 不崩。"""

    def test_observe_noop_without_key(self, monkeypatch):
        """未配置 Langfuse key -> observe 装饰器 no-op，函数正常执行。"""
        from app.core import langfuse_client
        monkeypatch.setattr(langfuse_client.settings, "langfuse_secret_key", "")
        monkeypatch.setattr(langfuse_client.settings, "langfuse_public_key", "")
        monkeypatch.setattr(langfuse_client, "_langfuse_instance", None)

        @langfuse_client.observe(name="e2e_test_fn")
        def some_business_logic(x):
            return x * 2

        # 主流程不受 Langfuse 影响
        assert some_business_logic(21) == 42
