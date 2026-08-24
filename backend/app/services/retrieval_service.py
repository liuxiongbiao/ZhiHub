"""
backend/app/services/retrieval_service.py

职责: 【全项目技术含量最高单点】混合检索完整编排--
      查询改写（补全指代） -> Milvus 向量召回 Top20 + ES BM25 召回 Top20
      -> RRF 融合排序 -> bge-reranker 精排 -> 置信度判断（低于阈值触发 HyDE 兜底）
      -> 空知识库分支判断 -> 降级模式标记

核心设计:
  双路召回 + RRF 融合 + 精排 + 兜底，是工业级 RAG 检索的标准范式。向量召回抓语义相似
  （"退款"能召回到"退货款"），BM25 抓关键词精确匹配（工单号、专有名词），两者互补。
  RRF 融合不用加权分数而用倒数排名，原因是两路分数尺度不同（向量是 IP 相似度 0~1，
  ES 是 BM25 score 可达几十），直接加权会被尺度大的那路主导；RRF 只看排名不看绝对分数，
  天然尺度无关。精排用 bge-reranker 做交叉编码，比双塔召回精度高但慢，所以只对融合后候选精排。

关键技术点:
  - 查询改写: LLM 补全指代（"它怎么退款" -> "XX 订单怎么退款"），提升召回率
  - 双路召回: Milvus 向量（语义）+ ES BM25（关键词），互补
  - RRF 融合: score=Σ 1/(k+rank)，k=60 是经验值，尺度无关
  - rerank 精排: bge-reranker 交叉编码，重排 Top5
  - HyDE 兜底: 低置信度时 LLM 生成假设文档再检索，缓解查询-文档表述差异
  - 空知识库分支: 租户无文档时直接返回标记，避免无意义检索
  - RetrievalResult: 数据类携带全链路中间结果，供 trace 和对比分析

依赖关系:
  - 上游: app/agents/nodes/knowledge_agent.py（调 hybrid_search 取知识）
  - 下游: app/core/embedding_client.py（embed+Milvus+ES）、app/core/rerank_client.py（精排）、
          app/core/langfuse_client.py（@observe 追踪）、litellm（改写/HyDE）
"""

import time
import logging
from dataclasses import dataclass, field

from app.config import settings
from app.core.embedding_client import (
    embed_texts,
    get_es_client,
    get_milvus_collection,
)
from app.core.langfuse_client import observe
from app.core.rerank_client import rerank as rerank_api

logger = logging.getLogger(__name__)

# ─── 常量 ───
VECTOR_TOP_K = 20     # 向量召回数量
BM25_TOP_K = 20       # BM25 召回数量
RRF_K = 60            # RRF 融合常数，经验值（缓解高排名项过度主导）
RERANK_TOP_K = 5      # 精排后保留数量
RERANK_CANDIDATE_LIMIT = 20  # 交叉编码器只处理融合后的高价值候选，控制冷链路延迟
# bge-reranker relevance_score 阈值：低于此值触发 HyDE 兜底
# 标定依据（2026-08-23 真实栈实验，bge-reranker-v2-m3 经硅基流动 API）：
#   最佳匹配片段 ≈ 0.359，弱相关/无关片段 ≤ 0.013。
# 原值 0.6 高于该模型实际分数上限，导致 100% 判低置信度、全部转人工（自动解决率 0%）。
# 0.3 vs 0.25 对比实验（18 题端到端）：结果完全一致（自动解决率 38.9%），
#   说明剩余漏答题的 rerank 分数已在噪声区（≤0.013），属检索召回问题而非阈值问题。
# 定稿 0.3：与噪声地板保持安全距离，后续可基于扩充评测集进一步标定。
CONFIDENCE_THRESHOLD = 0.3

# 规格 008 降级模式告警: 检索召回失败时 Redis 标记 degradation:{tenant_id}，TTL 1h（PRD §3.4/3.8）
DEGRADATION_KEY_PREFIX = "degradation:"
DEGRADATION_EVENT_KEY_PREFIX = "degradation:event:"
DEGRADATION_TTL = 3600  # 1 小时，平衡敏感度与抖动


def _mark_degradation(tenant_id: int, affected_service: str = "retrieval") -> None:
    """检索降级标记：vector/bm25 召回失败时设置 Redis 标记（TTL 1h）。

    /home 端点读取此标记，前端据此显示降级告警横幅。
    TTL 1h 是平衡选择: 太短用户看不到，太长故障恢复后仍显示。
    Redis 不可用时静默失败（不影响检索主流程）。
    """
    try:
        from app.core.redis import get_redis
        redis_client = get_redis()
        redis_client.set(f"{DEGRADATION_KEY_PREFIX}{tenant_id}", "1", ex=DEGRADATION_TTL)
        started_at = str(time.time())
        first_entry = redis_client.set(
            f"{DEGRADATION_EVENT_KEY_PREFIX}{tenant_id}:{affected_service}",
            started_at,
            ex=DEGRADATION_TTL,
            nx=True,
        )
        if first_entry:
            from app.services.analytics_service import track_event
            track_event(
                "degradation_mode_entered",
                tenant_id=tenant_id,
                properties={"affected_service": affected_service},
            )
    except Exception:
        pass


def _mark_recovery(tenant_id: int, affected_service: str) -> None:
    """单路召回恢复后记录退出事件；两个召回服务都恢复才清除全局告警。"""
    try:
        from app.core.redis import get_redis
        redis_client = get_redis()
        event_key = f"{DEGRADATION_EVENT_KEY_PREFIX}{tenant_id}:{affected_service}"
        started_at = redis_client.get(event_key)
        if started_at is None:
            return
        redis_client.delete(event_key)
        if not any(redis_client.scan_iter(f"{DEGRADATION_EVENT_KEY_PREFIX}{tenant_id}:*", count=10)):
            redis_client.delete(f"{DEGRADATION_KEY_PREFIX}{tenant_id}")
        from app.services.analytics_service import track_event
        track_event(
            "degradation_mode_exited",
            tenant_id=tenant_id,
            properties={
                "affected_service": affected_service,
                "duration": max(0, round(time.time() - float(started_at), 3)),
            },
        )
    except Exception:
        pass


# ─── 数据类 ───
@dataclass
class ChunkResult:
    """单个检索切片结果。source 标记来源（vector/bm25/hybrid/hyde），便于对比分析。"""
    doc_id: int
    doc_filename: str
    chunk_index: int
    chunk_text: str
    score: float
    source: str  # "vector" | "bm25" | "hybrid" | "hyde"


@dataclass
class RetrievalResult:
    """完整检索结果，携带全链路中间状态供 trace 与上层决策。

    字段说明:
      - hybrid_results: 主结果（RRF+rerank 后的 Top5）
      - vector_results: 纯向量对比基线（不经融合精排，用于 A/B 对比混合检索收益）
      - hyde_fallback: 是否触发了 HyDE 兜底
      - empty_knowledge: 知识库是否为空（上层据此给"未上传知识"提示而非"未找到"）
    """
    query: str
    rewritten_query: str | None
    vector_results: list[ChunkResult] = field(default_factory=list)
    hybrid_results: list[ChunkResult] = field(default_factory=list)
    hyde_fallback: bool = False
    low_confidence: bool = False
    empty_knowledge: bool = False
    elapsed_ms: int = 0
    timings_ms: dict[str, int] = field(default_factory=dict)


# ─── 查询改写 ───
def _normalize_colloquial_query(query: str) -> str:
    """按“业务实体 + 口语意图”补知识库术语，不靠降低 0.3 阈值兜底。"""
    normalized = query.strip()
    expansions: list[str] = []
    if "登录" in normalized and any(
        phrase in normalized for phrase in ("转圈", "卡住", "卡顿", "进不去", "打不开")
    ):
        expansions.append("登录页面加载缓慢 登录失败 登录异常 排查 解决方案")
    if any(entity in normalized for entity in ("方案", "套餐", "版本")) and any(
        phrase in normalized for phrase in ("便宜", "省钱", "划算", "低价")
    ):
        expansions.append("套餐价格 试用版 基础版 专业版 月费 价格对比 降级")
    if not expansions:
        return normalized
    return f"{normalized} {' '.join(expansions)}"


def _needs_contextual_rewrite(query: str) -> bool:
    """判断当前问句是否含依赖历史的指代或省略。"""
    reference_phrases = (
        "它",
        "这个",
        "那个",
        "这项",
        "那项",
        "上面",
        "刚才",
        "之前说的",
        "这种",
        "那种",
    )
    if any(phrase in query for phrase in reference_phrases):
        return True
    return len(query.strip()) <= 12 and query.strip().endswith(
        ("的", "呢", "吗", "可以吗", "怎么办", "多久", "多少")
    )


def rewrite_query(history: list[dict], current_query: str) -> str:
    """指代补全：用 LLM 将对话历史中的省略/指代补全为独立检索查询。

    流程:
      1. 无历史 -> 直接返回原查询
      2. 取最近 3 轮历史拼 prompt
      3. LLM 改写（temperature=0 保证稳定）
      4. 防御：改写结果非空且 <=500 字才采用，否则回退原查询

    设计决策:
      指代补全是多轮对话检索的关键--用户说"它怎么退款"时，"它"指代上一轮的订单，
      不补全直接检索"它怎么退款"会召回大量无关结果。temperature=0 让改写稳定可复现。
      失败不抛异常，回退原查询保证检索链路不中断（检索降级而非失败）。

    如果历史为空或不需要改写，直接返回原查询。
    """
    normalized_query = _normalize_colloquial_query(current_query)
    if not settings.llm_enabled or not history:
        return normalized_query
    if not _needs_contextual_rewrite(current_query):
        return normalized_query

    # 构建改写 prompt，只取最近 3 轮避免上下文过长
    history_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
        for m in history[-3:]  # 最近 3 轮
    )

    prompt = (
        "根据对话历史，将用户的当前查询改写为独立、完整的检索查询。\n"
        "只补全指代和省略；同时把口语化故障、价格说法映射为知识库常用术语，"
        "例如‘一直转圈’对应‘页面加载缓慢/登录异常’，‘便宜方案’对应‘套餐价格对比’。\n\n"
        f"对话历史：\n{history_text}\n\n"
        f"当前查询：{normalized_query}\n\n"
        "改写后的查询："
    )

    try:
        from app.core.llm_client import llm_complete

        resp = llm_complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.0,  # 0 保证改写稳定可复现
        )
        rewritten = resp.choices[0].message.content.strip()
        # 防御：改写结果不应为空或过长（过长可能是 LLM 跑题）
        if rewritten and len(rewritten) <= 500:
            logger.info(f"Query rewritten: '{current_query}' -> '{rewritten}'")
            return rewritten
    except Exception as e:
        # 改写失败不影响主流程，回退原查询
        logger.warning(f"Query rewrite failed, using original: {e}")

    return normalized_query


# ─── Milvus 向量召回 ───
def vector_recall(query: str, tenant_id: int, top_k: int = VECTOR_TOP_K) -> list[dict]:
    """Milvus 向量相似度召回（语义匹配）。

    流程:
      1. 查询向量化（embed_texts）
      2. Milvus search，按 tenant_id 过滤（partition key 分区裁剪），IP 度量，nprobe=16
      3. 转换结果为统一 dict 格式

    设计决策:
      nprobe=16 控制探针数，IVF_FLAT 索引下 nprobe 越大召回越全但越慢，16 是精度/速度折中。
      tenant_id 过滤用 expr 实现，配合 partition_key 做物理分区裁剪，租户隔离 + 加速。
      Milvus 异常返回空列表而非抛异常，保证单路失败不拖垮整个混合检索（另一路 BM25 仍可用）。

    Returns:
        [{"doc_id": int, "chunk_index": int, "chunk_text": str, "score": float, ...}, ...]
    """
    if not settings.embedding_enabled:
        logger.info("Embedding disabled: skip vector recall for tenant %s", tenant_id)
        return []

    collection = get_milvus_collection()

    # 1. 查询向量化
    vectors = embed_texts([query])
    if not vectors:
        return []
    query_vec = vectors[0]

    # 2. Milvus 搜索（按 tenant_id partition key 过滤）
    try:
        results = collection.search(
            data=[query_vec],
            anns_field="vector",
            param={"metric_type": "IP", "params": {"nprobe": 16}},
            limit=top_k,
            expr=f"tenant_id == {tenant_id}",
            output_fields=["doc_id", "chunk_index", "chunk_text", "doc_filename"],
        )
    except Exception as e:
        # 单路失败不中断：返回空，让 BM25 路兜底
        logger.error(f"Milvus search failed: {e}")
        _mark_degradation(tenant_id, "milvus")  # 规格 008: 降级标记（PRD 3.4/3.8）
        return []

    # 3. 转换结果
    output = []
    if results and len(results) > 0:
        for hit in results[0]:
            output.append({
                "doc_id": hit.entity.get("doc_id"),
                "chunk_index": hit.entity.get("chunk_index"),
                "chunk_text": hit.entity.get("chunk_text", ""),
                "doc_filename": hit.entity.get("doc_filename", ""),
                "score": hit.distance,  # IP 相似度
                "source": "vector",
            })
    _mark_recovery(tenant_id, "milvus")
    return output


# ─── ES BM25 召回 ───
def bm25_recall(query: str, tenant_id: int, top_k: int = BM25_TOP_K) -> list[dict]:
    """Elasticsearch BM25 全文检索召回（关键词匹配）。

    流程:
      1. ES search，must=chunk_text match（BM25 打分）
      2. filter=tenant_id + is_latest（租户隔离 + 只查最新版切片）
      3. 转换结果

    设计决策:
      is_latest=True 过滤是文档版本管理--同一文档重新解析后旧切片标记 is_latest=false，
      检索只命中最新版，避免召回过期内容。tenant_id 用 term 精确过滤。
      ES 异常同样返回空列表，保证单路失败不影响另一路。

    Returns:
        [{"doc_id": int, "chunk_index": int, "chunk_text": str, "score": float, ...}, ...]
    """
    es = get_es_client()

    try:
        resp = es.search(
            index="knowledge_chunks",
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"chunk_text": query}},
                        ],
                        "filter": [
                            {"term": {"tenant_id": tenant_id}},
                            {"term": {"is_latest": True}},
                        ],
                    }
                },
                "size": top_k,
            },
        )
    except Exception as e:
        logger.error(f"ES search failed: {e}")
        _mark_degradation(tenant_id, "elasticsearch")  # 规格 008: 降级标记（PRD 3.4/3.8）
        return []

    output = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        output.append({
            "doc_id": src.get("doc_id"),
            "chunk_index": src.get("chunk_index"),
            "chunk_text": src.get("chunk_text", ""),
            "doc_filename": src.get("doc_filename", ""),
            "score": hit["_score"],
            "source": "bm25",
        })
    _mark_recovery(tenant_id, "elasticsearch")
    return output


# ─── RRF 融合 ───
def rrf_fuse(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = RRF_K,
) -> list[dict]:
    """RRF (Reciprocal Rank Fusion) 融合两路召回结果。

    流程:
      1. 对每路结果按排名计算 rrf_score = 1/(k+rank)
      2. 同一切片（doc_id+chunk_index 唯一键）的分数累加
      3. 按累加分数降序去重输出

    设计决策:
      用 RRF 不用加权分数，是因为两路分数尺度不同：向量是 IP 相似度（0~1），ES 是 BM25 score
      （可达几十），直接加权会被 ES 主导。RRF 只看每路的排名，1/(k+rank) 把排名转成无尺度分数，
      天然公平。k=60 是论文经验值，缓解排名 1 的项过度主导（rank=1 时 1/61，rank=2 时 1/62，
      差距被 k 拉平）。

    score(d) = Σ 1/(k + rank_i(d))

    按工 RRF 分数降序返回去重后的结果列表。
    """
    # 构建 chunk key → RRF 分数
    rrf_scores: dict[tuple, float] = {}
    # 保存完整信息（首次出现的切片信息保留）
    chunk_info: dict[tuple, dict] = {}

    def add_results(results: list[dict], result_source: str):
        for rank, item in enumerate(results, start=1):
            # (doc_id, chunk_index) 唯一标识一个切片，跨路去重
            key = (item["doc_id"], item["chunk_index"])
            rrf_score = 1.0 / (k + rank)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + rrf_score
            if key not in chunk_info:
                chunk_info[key] = {**item, "source": result_source}

    add_results(vector_results, "vector")
    add_results(bm25_results, "bm25")

    # 按 RRF 分数降序排列
    fused = []
    for key, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
        info = chunk_info[key].copy()
        info["rrf_score"] = score
        info["doc_id"], info["chunk_index"] = key
        fused.append(info)

    return fused


# ─── HyDE 兜底 ───
def _hyde_retrieval(query: str, tenant_id: int) -> list[dict]:
    """HyDE 兜底：用 LLM 生成假设文档 -> embedding -> 二次向量检索。

    流程:
      1. LLM 生成一段假设性回答文档（temperature=0.3 允许一定发散）
      2. 用假设文档的 embedding 做向量召回

    设计决策:
      HyDE (Hypothetical Document Embeddings) 解决"查询与文档表述差异"问题--用户问"怎么退款"，
      知识库文档可能写成"退款申请流程"，直接用查询 embedding 召回相似度低。让 LLM 先生成一段
      假设的回答（更接近文档表述），再用它去匹配文档，召回率显著提升。这是低置信度时的兜底手段，
      代价是多一次 LLM 调用，所以只在 Top1 < 阈值时触发。
    """
    if not settings.llm_enabled or not settings.embedding_enabled:
        return []

    hyde_prompt = (
        "请生成一段可能相关的中文文档内容来回答以下问题。"
        "生成的内容应该是假设性的、信息丰富的，长度约 100-200 字。\n\n"
        f"问题：{query}\n\n"
        "假设文档内容："
    )

    try:
        from app.core.llm_client import llm_complete

        resp = llm_complete(
            messages=[{"role": "user", "content": hyde_prompt}],
            max_tokens=300,
            temperature=0.3,  # 0.3 允许一定发散，生成更丰富的假设文档
        )
        hypothetical_doc = resp.choices[0].message.content.strip()
        if not hypothetical_doc:
            return []
    except Exception as e:
        logger.warning(f"HyDE generation failed: {e}")
        return []

    # 用假设文档的 embedding 做向量检索（注意是用假设文档而非原 query）
    return vector_recall(hypothetical_doc, tenant_id, top_k=RERANK_TOP_K)


# ─── 检查知识库是否为空 ───
def _is_knowledge_base_empty(tenant_id: int) -> bool:
    """检查租户知识库是否存在 ready 状态的文档。

    流程:
      1. 先查 ES（轻量，size=1 只看 total）
      2. ES 失败 fallback 查 Milvus
      3. 全失败假定不为空（宁可搜不到也不要错误标记空）

    设计决策:
      空知识库单独分支是为了给用户更准确的反馈--"您还未上传知识文档"比"未找到相关内容"
      更有帮助。全失败时返回 False（假定不为空）是保守策略：错标为空会让用户以为没上传文档，
      而错标为非空最多是搜不到结果，后者危害更小。
    """
    es = get_es_client()
    try:
        resp = es.search(
            index="knowledge_chunks",
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"tenant_id": tenant_id}},
                            {"term": {"is_latest": True}},
                        ]
                    }
                },
                "size": 1,
            },
        )
        total = resp["hits"]["total"]
        # ES 版本差异：total 可能是 dict（含 value）或直接是 int
        count = total["value"] if isinstance(total, dict) else total
        return count == 0
    except Exception:
        pass

    # ES 查询失败的 fallback：查 Milvus
    try:
        collection = get_milvus_collection()
        # 用一条空查询检查是否有数据
        results = collection.query(
            expr=f"tenant_id == {tenant_id}",
            limit=1,
            output_fields=["doc_id"],
        )
        return len(results) == 0
    except Exception:
        # 全失败时假定不为空（宁可搜不到也不要错误标记空）
        return False


# ─── 主编排函数 ───
def _track_retrieval_result(tenant_id: int, result: RetrievalResult) -> None:
    """记录可用于纯向量/混合/HyDE 对比的同一次检索结果。"""
    from app.services.analytics_service import track_event

    track_event(
        "retrieval_executed",
        tenant_id=tenant_id,
        properties={
            "query": result.query,
            "confidence": (
                result.hybrid_results[0].score if result.hybrid_results else 0
            ),
            "latency": result.elapsed_ms,
            "mode": "hyde" if result.hyde_fallback else "hybrid",
            "vector_top_score": (
                result.vector_results[0].score if result.vector_results else None
            ),
            "hybrid_top_score": (
                result.hybrid_results[0].score if result.hybrid_results else None
            ),
            "vector_result_count": len(result.vector_results),
            "hybrid_result_count": len(result.hybrid_results),
            "hyde_fallback": result.hyde_fallback,
            "low_confidence": result.low_confidence,
            "empty_knowledge": result.empty_knowledge,
            "timings_ms": result.timings_ms,
        },
    )


@observe(name="retrieval")
def hybrid_search(
    query: str,
    tenant_id: int,
    history: list[dict] | None = None,
) -> RetrievalResult:
    """混合检索主编排。

    流程:
      1. 查询改写（指代补全）
      2. 检查知识库是否为空 -> 空则直接返回
      3. 向量召回 Top20 + BM25 召回 Top20
      4. RRF 融合
      5. Reranker 精排 Top5 -> 混合检索结果
      6. 纯向量结果：直接取向量召回 Top5（仅用于对比基线）
      7. 置信度判断：Top1 < 阈值 -> HyDE 兜底（二次融合重排）
      8. 返回完整 RetrievalResult

    设计决策:
      @observe 追踪整个检索耗时与链路，便于 Langfuse 分析检索性能。
    """
    start = time.perf_counter()
    history = history or []
    timings: dict[str, int] = {}

    # 1. 查询改写
    stage_start = time.perf_counter()
    rewritten = rewrite_query(history, query)
    timings["rewrite"] = int((time.perf_counter() - stage_start) * 1000)

    # 2. 空知识库检查
    stage_start = time.perf_counter()
    if _is_knowledge_base_empty(tenant_id):
        timings["knowledge_check"] = int((time.perf_counter() - stage_start) * 1000)
        elapsed = int((time.perf_counter() - start) * 1000)
        result = RetrievalResult(
            query=query,
            rewritten_query=rewritten if rewritten != query else None,
            empty_knowledge=True,
            elapsed_ms=elapsed,
            timings_ms=timings,
        )
        _track_retrieval_result(tenant_id, result)
        return result
    timings["knowledge_check"] = int((time.perf_counter() - stage_start) * 1000)

    # 3. 双路召回（ThreadPoolExecutor 真正并行，避免两路延迟叠加）
    from concurrent.futures import ThreadPoolExecutor
    stage_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        fv = ex.submit(vector_recall, rewritten, tenant_id)
        fb = ex.submit(bm25_recall, rewritten, tenant_id)
        vector_raw = fv.result()
        bm25_raw = fb.result()
    timings["parallel_recall"] = int((time.perf_counter() - stage_start) * 1000)

    # 4. RRF 融合
    stage_start = time.perf_counter()
    fused = rrf_fuse(vector_raw, bm25_raw)
    timings["rrf_fusion"] = int((time.perf_counter() - stage_start) * 1000)

    # 5. Reranker 精排
    hybrid_top = []
    hyde_fallback = False
    low_confidence = False
    rerank_elapsed = 0
    hyde_elapsed = 0
    if fused and not settings.embedding_enabled:
        # embedding/rerank 同属向量能力开关；关闭时保留 BM25+RRF 结果，
        # 不调用外部 reranker，也不触发依赖 LLM+向量的 HyDE。
        for item in fused[:RERANK_TOP_K]:
            hybrid_top.append(ChunkResult(
                doc_id=item.get("doc_id", 0),
                doc_filename=item.get("doc_filename", ""),
                chunk_index=item.get("chunk_index", 0),
                chunk_text=item.get("chunk_text", ""),
                score=item.get("rrf_score", item.get("score", 0.0)),
                source=item.get("source", "bm25"),
            ))
    if fused and settings.embedding_enabled:
        # 提取去重的候选文本，送 reranker 交叉编码精排
        rerank_start = time.perf_counter()
        rerank_pool = fused[:RERANK_CANDIDATE_LIMIT]
        candidates = [c["chunk_text"] for c in rerank_pool]
        reranked = rerank_api(query=rewritten, documents=candidates, top_n=RERANK_TOP_K)

        for r in reranked[:RERANK_TOP_K]:
            idx = r["index"]
            if idx < len(rerank_pool):
                item = rerank_pool[idx]
                hybrid_top.append(ChunkResult(
                    doc_id=item.get("doc_id", 0),
                    doc_filename=item.get("doc_filename", ""),
                    chunk_index=item.get("chunk_index", 0),
                    chunk_text=item.get("chunk_text", ""),
                    score=r.get("relevance_score", 0.0),
                    source="hybrid",
                ))
        rerank_elapsed += int((time.perf_counter() - rerank_start) * 1000)

        # 6. 置信度判断：Top1 < 阈值 → HyDE 兜底
        if hybrid_top and hybrid_top[0].score < CONFIDENCE_THRESHOLD:
            logger.info(
                f"Low confidence (top1={hybrid_top[0].score:.3f}), triggering HyDE fallback"
            )
            hyde_fallback = True
            hyde_start = time.perf_counter()
            hyde_results = _hyde_retrieval(query, tenant_id)

            if hyde_results:
                # 二次 RRF 融合（原融合结果 + HyDE 结果），再精排一次
                re_fused = rrf_fuse(fused, hyde_results)
                re_rerank_start = time.perf_counter()
                re_rerank_pool = re_fused[:RERANK_CANDIDATE_LIMIT]
                re_candidates = [c["chunk_text"] for c in re_rerank_pool]
                re_reranked = rerank_api(
                    query=rewritten, documents=re_candidates, top_n=RERANK_TOP_K
                )
                rerank_elapsed += int((time.perf_counter() - re_rerank_start) * 1000)
                hybrid_top = []
                for r in re_reranked[:RERANK_TOP_K]:
                    idx = r["index"]
                    if idx < len(re_rerank_pool):
                        item = re_rerank_pool[idx]
                        hybrid_top.append(ChunkResult(
                            doc_id=item.get("doc_id", 0),
                            doc_filename=item.get("doc_filename", ""),
                            chunk_index=item.get("chunk_index", 0),
                            chunk_text=item.get("chunk_text", ""),
                            score=r.get("relevance_score", 0.0),
                            source="hyde",
                        ))
            hyde_elapsed = int((time.perf_counter() - hyde_start) * 1000)

        # PRD §3.3：HyDE 重试后置信度仍低于阈值 → 标记低置信度，供上层 Agent 决策转人工
        if hybrid_top and hybrid_top[0].score < CONFIDENCE_THRESHOLD:
            low_confidence = True
    timings["rerank"] = rerank_elapsed
    if hyde_fallback:
        timings["hyde"] = hyde_elapsed

    # 纯向量对比基线：取向量召回 Top5（不经 RRF/rerank），用于 A/B 对比混合检索的收益
    vector_top = []
    for item in vector_raw[:RERANK_TOP_K]:
        vector_top.append(ChunkResult(
            doc_id=item.get("doc_id", 0),
            doc_filename=item.get("doc_filename", ""),
            chunk_index=item.get("chunk_index", 0),
            chunk_text=item.get("chunk_text", ""),
            score=item.get("score", 0.0),
            source="vector",
        ))

    elapsed = int((time.perf_counter() - start) * 1000)

    result = RetrievalResult(
        query=query,
        rewritten_query=rewritten if rewritten != query else None,
        vector_results=vector_top,
        hybrid_results=hybrid_top,
        hyde_fallback=hyde_fallback,
        low_confidence=low_confidence,
        elapsed_ms=elapsed,
        timings_ms=timings,
    )
    _track_retrieval_result(tenant_id, result)
    return result
