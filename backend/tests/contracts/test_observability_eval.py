"""
backend/tests/contracts/test_observability_eval.py

职责: 测试 规格 008 可观测性与效果评测的契约（PII 脱敏 / Langfuse 降级 / RAGAS helpers）

测试内容:
  - TestMaskPii: mask_pii 手机号脱敏（138****0001），不误伤工单号/订单号，不可逆
  - TestLangfuseClientDegradation: Langfuse 未配置 key -> no-op，主流程不崩
  - TestRagasEvalHelpers: RAGAS 评测脚本 helper（解析 eval_questions.md + 格式化结果表）
"""

import pytest


# ═══════════════════════════════════════════════════════════
# T011: PII 脱敏（User Story 3）
# SC-003: trace 中手机号 100% 脱敏（138****0001），不误伤编号
# ═══════════════════════════════════════════════════════════
class TestMaskPii:
    """mask_pii 手机号脱敏契约。"""

    def test_phone_masked(self):
        from app.utils.desensitize import mask_pii
        assert mask_pii("我的手机是13800000001") == "我的手机是138****0001"

    def test_phone_alone(self):
        from app.utils.desensitize import mask_pii
        assert mask_pii("13800000001") == "138****0001"

    def test_multiple_phones(self):
        from app.utils.desensitize import mask_pii
        assert mask_pii("13800000001 和 13912345678") == "138****0001 和 139****5678"

    def test_ticket_no_not_masked(self):
        r"""工单号 TK-... 不被误伤（不匹配 1[3-9]\d{9} 整词）。"""
        from app.utils.desensitize import mask_pii
        text = "查工单 TK-20240001，订单 ORD-20240001"
        assert mask_pii(text) == text  # 无变化

    def test_order_no_with_digits_not_masked(self):
        """ORD-20240001 含连续数字但不是手机号格式，不脱敏。"""
        from app.utils.desensitize import mask_pii
        assert mask_pii("ORD-20240001") == "ORD-20240001"

    def test_empty_and_none(self):
        from app.utils.desensitize import mask_pii
        assert mask_pii("") == ""
        assert mask_pii(None) is None  # type: ignore[arg-type]

    def test_no_phone_unchanged(self):
        from app.utils.desensitize import mask_pii
        assert mask_pii("如何升级套餐") == "如何升级套餐"

    def test_non_chinese_context(self):
        """英文上下文中的手机号也脱敏。"""
        from app.utils.desensitize import mask_pii
        assert mask_pii("call me at 13800000001 pls") == "call me at 138****0001 pls"

    def test_irreversible(self):
        """脱敏后中间4位丢失，不可还原。"""
        from app.utils.desensitize import mask_pii
        masked = mask_pii("13800000001")
        assert "0000" not in masked.replace("****", "")  # 中间4位不出现
        assert masked == "138****0001"


# ═══════════════════════════════════════════════════════════
# T007: Langfuse client 降级（User Story 2）
# SC-005: Langfuse 未配置/失败时主流程 100% 不受影响
# ═══════════════════════════════════════════════════════════
class TestLangfuseClientDegradation:
    """Langfuse client 降级契约（未配置 key -> no-op，不抛异常）。"""

    def test_get_langfuse_no_key_returns_none(self, monkeypatch):
        """未配置 langfuse_secret_key -> get_langfuse 返回 None（降级 no-op）。"""
        from app.core import langfuse_client
        # 模拟未配置 key
        monkeypatch.setattr(langfuse_client.settings, "langfuse_secret_key", "")
        monkeypatch.setattr(langfuse_client.settings, "langfuse_public_key", "")
        # 重置单例
        monkeypatch.setattr(langfuse_client, "_langfuse_instance", None)
        assert langfuse_client.get_langfuse() is None

    def test_observe_decorator_noop_without_langfuse(self, monkeypatch):
        """无 Langfuse 时 observe 装饰器仍正常执行被装饰函数。"""
        from app.core import langfuse_client
        monkeypatch.setattr(langfuse_client.settings, "langfuse_secret_key", "")
        monkeypatch.setattr(langfuse_client, "_langfuse_instance", None)

        @langfuse_client.observe(name="test_fn")
        def add(a, b):
            return a + b

        assert add(2, 3) == 5  # 函数正常执行，不抛异常

    def test_observe_preserves_return_value(self, monkeypatch):
        from app.core import langfuse_client
        monkeypatch.setattr(langfuse_client.settings, "langfuse_secret_key", "")
        monkeypatch.setattr(langfuse_client, "_langfuse_instance", None)

        @langfuse_client.observe(name="echo")
        def echo(x):
            return {"value": x}

        assert echo("hello") == {"value": "hello"}


# ═══════════════════════════════════════════════════════════
# T004: RAGAS 评测 helpers（User Story 1）
# 手动实现 RAGAS 三指标（ragas 包在 Python 3.14 不可用，改 LLM-as-judge）
# 契约: 解析 eval_questions.md + 格式化量化结果表
# ═══════════════════════════════════════════════════════════
class TestRagasEvalHelpers:
    """RAGAS 评测脚本 helper 契约（无 Docker/LLM 依赖）。"""

    SAMPLE_MD = """# 检索效果评测问题集

## 精确匹配类（预期BM25优势）

| # | 问题 | 预期 |
|---|---|---|
| Q1 | 工单编号TK-20240001的当前状态是什么？ | BM25精确命中工单号 |
| Q2 | 订单ORD-20240002的金额是多少？ | BM25精确命中订单号 |

## 语义匹配类（预期向量优势）

| # | 问题 | 预期 |
|---|---|---|
| Q6 | 怎么把现在的套餐换成更高级的？ | 向量理解"换成更高级"="升级" |
"""

    def test_parse_eval_questions(self):
        from scripts.ragas_eval import parse_eval_questions
        questions = parse_eval_questions(self.SAMPLE_MD)
        assert len(questions) == 3
        assert questions[0]["category"] == "精确匹配类"
        assert "TK-20240001" in questions[0]["question"]
        assert questions[0]["expected"] == "BM25精确命中工单号"
        assert questions[2]["category"] == "语义匹配类"

    def test_parse_real_eval_questions(self):
        """真实 eval_questions.md 能解析到 18 题。"""
        import os
        from scripts.ragas_eval import parse_eval_questions
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "eval_questions.md")
        with open(path, encoding="utf-8") as f:
            questions = parse_eval_questions(f.read())
        assert len(questions) >= 15  # 至少 15 题（实际 18）

    def test_format_results_table(self):
        from scripts.ragas_eval import format_results_table
        results = [
            {"q": "Q1", "category": "精确", "method": "vector", "faithfulness": 0.8, "answer_relevancy": 0.7, "context_recall": 0.5},
            {"q": "Q1", "category": "精确", "method": "hybrid", "faithfulness": 0.9, "answer_relevancy": 0.85, "context_recall": 0.8},
        ]
        table = format_results_table(results)
        assert "| Q1 |" in table
        assert "vector" in table
        assert "hybrid" in table
        assert "0.80" in table or "0.8" in table