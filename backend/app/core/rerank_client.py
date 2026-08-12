"""
backend/app/core/rerank_client.py

职责: 硅基流动 bge-reranker-v2-m3 Rerank API 封装

核心设计:
  RAG 检索二阶段: 先用向量检索（Milvus）召回 Top-K 候选，再用 reranker 精排取 Top-N。
  reranker 比纯向量相似度更准（交叉注意力 vs 点积），但慢且贵，故只对小规模候选集精排。

关键技术点:
  - 为什么需要 rerank: 向量检索（双塔模型）用 embedding 点积近似相关性，速度快但精度有限；
    reranker（交叉编码器）将 query 和 document 拼接输入模型，捕获细粒度交互，
    精度更高但计算量大，只用于小规模候选集（Top-K -> Top-N）。
  - 重试 + 指数退避: 网络抖动/API 限流时自动重试，间隔 1s/2s/4s（2^attempt），
    避免雪崩。全部失败降级返回原始顺序（分数 0），保证检索链路不中断。
  - 降级策略: rerank 失败时返回原始文档顺序（分数 0），上层仍可展示结果，
    牺牲精度保证可用性（graceful degradation）。

依赖关系:
  - 上游: app/services/knowledge_service.py（RAG 检索流程）
  - 下游: 硅基流动 Rerank API（httpx 调用）
"""

import time

import httpx

from app.config import settings

MAX_RETRIES = 3
RERANK_API_URL = "https://api.siliconflow.cn/v1/rerank"


def _get_headers() -> dict[str, str]:
    """构造 API 请求头（Bearer token 认证）。"""
    return {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }


def rerank(query: str, documents: list[str], top_n: int = 5) -> list[dict]:
    """对候选文档列表做精排，返回按相关性降序排列的结果。

    流程: POST rerank API（query + documents + top_n）-> 返回 results 列表。
    失败自动重试（指数退避 1s/2s/4s），全部失败降级返回原始顺序（分数 0）。

    设计决策:
      - 重试而非直接失败: 外部 API 不可控（网络抖动/限流），重试提高成功率。
      - 指数退避: 2^attempt 秒间隔，避免重试风暴加剧 API 压力。
      - 降级返回原始顺序: rerank 是精度优化而非正确性保障，
        失败时返回原始候选仍可用，优于直接报错中断检索。

    Args:
        query: 用户查询
        documents: 候选文档文本列表（通常来自向量检索 Top-K）
        top_n: 返回 Top N 结果

    Returns:
        list of dicts: [{"index": int, "text": str, "relevance_score": float}, ...]
        正常时 API 返回 {index, relevance_score}，降级时补 text 且分数为 0
    """
    if not documents:
        return []

    headers = _get_headers()

    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.post(
                RERANK_API_URL,
                headers=headers,
                json={
                    "model": settings.rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["results"]  # list of {index, relevance_score}
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = 2**attempt
                time.sleep(wait)
                continue
            # 最终失败降级：返回原始文档顺序，分数设为 0（保证检索链路不中断）
            return [
                {"index": i, "text": doc, "relevance_score": 0.0}
                for i, doc in enumerate(documents[:top_n])
            ]
