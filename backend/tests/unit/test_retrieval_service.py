"""
backend/tests/unit/test_retrieval_service.py

职责: 测试混合检索引擎（查询改写、RRF 融合、空知识库、检索结果数据类）
对应日程: 002-knowledge-base

测试内容:
  - TestRewriteQuery: 无历史返回原查询 / 有历史补全指代 / 完整查询不大改
  - TestRRFFuse: 两路融合 / 同 chunk 去重 / 空输入 / RRF 分数降序
  - TestRetrievalResult: 空知识库标记 + 正常结果字段
"""

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
