"""
backend/tests/unit/test_retrieval_service.py

职责: 测试混合检索引擎（查询改写、RRF 融合、空知识库、检索结果数据类）

测试内容:
  - TestRewriteQuery: 无历史返回原查询 / 有历史补全指代 / 完整查询不大改
  - TestRRFFuse: 两路融合 / 同 chunk 去重 / 空输入 / RRF 分数降序
  - TestRetrievalResult: 空知识库标记 + 正常结果字段
"""

import sys
import types

import pytest

from app.services.retrieval_service import (
    rrf_fuse,
    rewrite_query,
    RetrievalResult,
    ChunkResult,
)


class TestRewriteQuery:
    """查询改写测试."""

    def test_no_history_returns_original(self):
        """无对话历史时直接返回原查询."""
        result = rewrite_query([], "如何升级套餐")
        assert result == "如何升级套餐"

    def test_with_history_rewrites_pronouns(self):
        """有对话历史时补全指代."""
        history = [
            {"role": "user", "content": "我想改套餐"},
            {"role": "assistant", "content": "您想升级还是降级？"},
        ]
        result = rewrite_query(history, "升级的")
        # LLM 改写结果不确定，但不应为空
        assert result is not None
        assert len(result) > 0

    def test_complete_query_not_rewritten_drastically(self):
        """完整查询不应被大幅改写."""
        history = [
            {"role": "user", "content": "有哪些文档？"},
        ]
        result = rewrite_query(history, "退款政策是什么")
        # 完整查询改写后应仍包含核心关键词
        assert "退款" in result or "政策" in result

    def test_colloquial_login_and_plan_terms_are_expanded(self):
        """口语化故障与价格问法补充知识库术语，不降低 0.3 阈值。"""
        login = rewrite_query([], "登录的时候一直转圈进不去")
        plan = rewrite_query([], "有没有便宜点的方案？")
        assert "登录异常" in login
        assert "套餐价格" in plan

    def test_unrelated_cheap_request_is_not_mapped_to_subscription_plan(self):
        """只有同时出现套餐实体时才扩展，避免把库外的便宜餐厅误映射为套餐。"""
        query = "附近有没有便宜点的餐厅？"
        assert rewrite_query([], query) == query

    def test_complete_query_with_history_skips_llm(self, monkeypatch):
        """已有历史但当前问句完整时不调用改写 LLM，减少一次外部调用。"""
        from app.services import retrieval_service

        monkeypatch.setattr(retrieval_service.settings, "llm_enabled", True)

        def _forbidden_llm(**kwargs):
            raise AssertionError("完整问句不应调用改写 LLM")

        monkeypatch.setitem(
            sys.modules,
            "app.core.llm_client",
            types.SimpleNamespace(llm_complete=_forbidden_llm),
        )
        result = rewrite_query(
            [{"role": "assistant", "content": "请继续提问"}],
            "登录一直转圈",
        )
        assert "登录页面加载缓慢" in result


class TestRRFFuse:
    """RRF 融合测试."""

    def test_basic_fusion(self):
        """两路结果正确融合."""
        vector = [
            {"doc_id": 1, "chunk_index": 0, "chunk_text": "A", "score": 0.9},
            {"doc_id": 2, "chunk_index": 0, "chunk_text": "B", "score": 0.8},
        ]
        bm25 = [
            {"doc_id": 2, "chunk_index": 0, "chunk_text": "B", "score": 5.0},
            {"doc_id": 1, "chunk_index": 0, "chunk_text": "A", "score": 3.0},
        ]
        result = rrf_fuse(vector, bm25)
        assert len(result) == 2

    def test_deduplication(self):
        """同一 chunk 在两路中出现应去重."""
        vector = [
            {"doc_id": 1, "chunk_index": 0, "chunk_text": "A", "score": 0.9},
        ]
        bm25 = [
            {"doc_id": 1, "chunk_index": 0, "chunk_text": "A", "score": 5.0},
        ]
        result = rrf_fuse(vector, bm25)
        assert len(result) == 1

    def test_empty_inputs(self):
        """空输入返回空列表."""
        assert rrf_fuse([], []) == []
        assert rrf_fuse([{"doc_id": 1, "chunk_index": 0, "chunk_text": "A", "score": 0.9}], []) == [
            {"doc_id": 1, "chunk_index": 0, "chunk_text": "A", "score": 0.9, "rrf_score": pytest.approx(1.0 / 61), "source": "vector"}
        ]

    def test_rrf_score_ordering(self):
        """RRF 分数应降序排列."""
        vector = [
            {"doc_id": 1, "chunk_index": 0, "chunk_text": "A", "score": 0.9},
        ]
        bm25 = [
            {"doc_id": 2, "chunk_index": 0, "chunk_text": "B", "score": 5.0},
        ]
        result = rrf_fuse(vector, bm25)
        assert result[0]["rrf_score"] >= result[1]["rrf_score"]


class TestRetrievalResult:
    """检索结果数据类测试."""

    def test_empty_knowledge_result(self):
        """空知识库结果应标记 empty_knowledge=True."""
        result = RetrievalResult(
            query="测试",
            rewritten_query=None,
            empty_knowledge=True,
        )
        assert result.empty_knowledge is True
        assert result.vector_results == []
        assert result.hybrid_results == []

    def test_normal_result(self):
        """正常检索结果."""
        result = RetrievalResult(
            query="测试",
            rewritten_query="改写后的测试",
            vector_results=[
                ChunkResult(
                    doc_id=1, doc_filename="a.md", chunk_index=0,
                    chunk_text="text", score=0.9, source="vector"
                )
            ],
            hybrid_results=[
                ChunkResult(
                    doc_id=1, doc_filename="a.md", chunk_index=0,
                    chunk_text="text", score=0.95, source="hybrid"
                )
            ],
            elapsed_ms=500,
        )
        assert result.elapsed_ms == 500
        assert len(result.vector_results) == 1
        assert len(result.hybrid_results) == 1


class TestRetrievalMockMode:
    def test_disabled_llm_and_embedding_use_keyword_path_without_external_calls(self, monkeypatch):
        from app.services import retrieval_service

        external_calls = {"llm": 0, "rerank": 0, "hyde": 0}

        def _forbidden_llm(**kwargs):
            external_calls["llm"] += 1
            raise AssertionError("LLM must stay disabled")

        monkeypatch.setitem(
            sys.modules,
            "app.core.llm_client",
            types.SimpleNamespace(llm_complete=_forbidden_llm),
        )
        monkeypatch.setattr(retrieval_service.settings, "llm_enabled", False)
        monkeypatch.setattr(retrieval_service.settings, "embedding_enabled", False)
        monkeypatch.setattr(retrieval_service, "_is_knowledge_base_empty", lambda tenant_id: False)
        monkeypatch.setattr(retrieval_service, "vector_recall", lambda *a, **k: [])
        monkeypatch.setattr(
            retrieval_service,
            "bm25_recall",
            lambda *a, **k: [{
                "doc_id": 7,
                "doc_filename": "refund.md",
                "chunk_index": 0,
                "chunk_text": "退款申请流程",
                "score": 3.2,
                "source": "bm25",
            }],
        )
        monkeypatch.setattr(
            retrieval_service,
            "rerank_api",
            lambda **kwargs: external_calls.__setitem__("rerank", external_calls["rerank"] + 1),
        )
        monkeypatch.setattr(
            retrieval_service,
            "_hyde_retrieval",
            lambda *a, **k: external_calls.__setitem__("hyde", external_calls["hyde"] + 1),
        )

        result = retrieval_service.hybrid_search(
            "退款",
            tenant_id=1,
            history=[{"role": "user", "content": "上一轮"}],
        )

        assert external_calls == {"llm": 0, "rerank": 0, "hyde": 0}
        assert result.rewritten_query is None
        assert len(result.hybrid_results) == 1
        assert result.hybrid_results[0].source == "bm25"
