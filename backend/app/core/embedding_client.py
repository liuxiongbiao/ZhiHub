"""
backend/app/core/embedding_client.py

职责: 文本向量化（硅基流动 bge-m3）+ Milvus/ES 存储结构初始化，是混合检索的底层基础设施。

核心设计:
  三件事：①调硅基流动 bge-m3 把文本变 1024 维向量（带重试）；②初始化 Milvus 的两个 collection--
  knowledge_chunks（知识库切片）和 user_memories（长期记忆）；③初始化 ES 的 knowledge_chunks 索引。
  Milvus 用 IVF_FLAT 索引 + IP（内积）度量，因为 bge-m3 输出已归一化，内积等价余弦相似度但计算更快。
  ES 用 ik_smart 中文分词做 BM25 召回。两者并行召回后由 retrieval_service 做 RRF 融合。

关键技术点:
  - bge-m3: 1024 维多语言向量模型，归一化后 IP 等价余弦
  - 指数退避重试: 1s/2s/4s 三次重试，应对 API 偶发抖动
  - IVF_FLAT + nlist=128: 倒排文件精确索引，适合 10k+ 切片规模，召回精度高
  - partition_key=tenant_id: Milvus 分区键实现租户物理隔离，查询自动带分区裁剪
  - 双 collection: 知识库与长期记忆分开存，schema 不同（记忆多 user_id/fact_type/created_at）

依赖关系:
  - 上游: app/tasks/knowledge_tasks.py（embed+双写）、app/services/retrieval_service.py（向量召回）、
          app/services/memory_service.py（记忆 upsert/检索）
  - 下游: app/config.py（API key/模型名/连接配置）、硅基流动 API、Milvus、Elasticsearch
"""

import time
from collections.abc import Sequence

import httpx
from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from app.config import settings

# ────────── 常量 ──────────
EMBEDDING_DIM = 1024  # bge-m3 向量维度，决定 Milvus FLOAT_VECTOR 的 dim
BATCH_SIZE = 16       # 批量 embedding 每批切片数，控制单次 API 请求体大小
MAX_RETRIES = 3       # embedding API 调用最大重试次数
COLLECTION_NAME = "knowledge_chunks"             # 知识库切片 collection
MEMORY_COLLECTION_NAME = "user_memories"  # 规格 009 长期记忆 collection


def _get_embedding_url() -> str:
    """硅基流动 embedding API 端点。"""
    return "https://api.siliconflow.cn/v1/embeddings"


def _get_headers() -> dict[str, str]:
    """构造带 Bearer token 的请求头。"""
    return {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }


# ────────── Embedding API ──────────
def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """批量调用 bge-m3 embedding API，返回与 texts 等长的向量列表。

    流程:
      1. 空输入直接返回空列表（避免无意义 API 调用）
      2. 最多重试 MAX_RETRIES 次，每次失败指数退避（1s/2s/4s）
      3. 校验返回向量数与输入数一致（防 API 截断返回）
      4. 全部重试失败抛 RuntimeError

    设计决策:
      指数退避而非固定间隔，是因为 API 限流/抖动通常需要递增等待；2**attempt 得到 1/2/4 秒，
      总等待不超过 7s，可接受。校验返回数量一致是关键--API 偶尔会漏返回部分向量，不校验会导致
      切片与向量错位，写入 Milvus 后检索结果张冠李戴。

    Args:
        texts: 待向量化的文本序列
    Returns:
        与 texts 等长的向量列表，每个向量是 1024 维 float 列表
    Raises:
        RuntimeError: MAX_RETRIES 次重试后仍失败
    """
    if not texts:
        return []

    url = _get_embedding_url()
    headers = _get_headers()

    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.post(
                url,
                headers=headers,
                json={
                    "model": settings.embedding_model,
                    "input": list(texts),
                    "encoding_format": "float",
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
            # 按输入顺序返回向量（API 保证 data 数组顺序与 input 一致）
            embeddings = [item["embedding"] for item in data["data"]]
            if len(embeddings) != len(texts):
                # 数量不一致：可能是 API 截断，视为失败触发重试
                raise ValueError(
                    f"Embedding API returned {len(embeddings)} vectors, expected {len(texts)}"
                )
            return embeddings
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = 2**attempt  # 指数退避：1s -> 2s -> 4s
                time.sleep(wait)
                continue
            # 最后一次仍失败：抛出，由调用方决定降级（如知识库任务记录失败切片）
            raise RuntimeError(f"Embedding API failed after {MAX_RETRIES} retries: {e}") from e


def embed_batches(texts: Sequence[str]) -> list[list[float]]:
    """将 texts 按 BATCH_SIZE 分批，逐批调用 embedding API，返回全部向量。

    流程:
      1. 按 BATCH_SIZE 切片
      2. 逐批调 embed_texts（每批内部自带重试）
      3. 累积所有向量

    设计决策:
      分批是为了控制单次 API 请求体大小--几百个切片一次性发可能超 body 限制或超时。
      注意：单批失败会抛异常中断整批，而非跳过继续。这是知识库场景的正确选择：宁可整批失败
      重试，也不要部分成功导致切片缺失（知识库完整性优先于部分可用）。
    """
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        vectors = embed_texts(batch)
        all_vectors.extend(vectors)
    return all_vectors


# ────────── Milvus Collection 管理 ──────────
def _connect_milvus():
    """建立 Milvus 连接（幂等，重复调用不会创建重复连接）。"""
    connections.connect(
        alias="default",
        host=settings.milvus_host,
        port=settings.milvus_port,
    )


def init_milvus_collection():
    """创建 knowledge_chunks Collection（如已存在则跳过）。

    流程:
      1. 连接 Milvus
      2. collection 已存在则直接返回（幂等，启动时可重复调）
      3. 定义 schema -> 创建 collection -> 建 IVF_FLAT 索引 -> load 到内存

    设计决策:
      tenant_id 设为 partition_key，Milvus 自动按租户分区，查询时带 tenant_id 过滤会做分区裁剪，
      物理隔离租户数据且加速查询。这是多租户隔离在向量库层的实现。
      索选用 IVF_FLAT（精确倒排）而非 HNSW，是精度优先--客服知识库规模不大（万级切片），
      IVF_FLAT 召回精度最高，HNSW 的图索引优势在大规模场景才体现。

    Schema:
      - id: INT64 auto_id primary key
      - tenant_id: INT64 partition key（租户隔离）
      - doc_id: INT64
      - chunk_index: INT64
      - chunk_text: VARCHAR(4096)
      - vector: FLOAT_VECTOR(1024)
    Index: IVF_FLAT, metric_type=IP（内积，bge-m3 归一化后等价余弦）
    """
    _connect_milvus()

    if utility.has_collection(COLLECTION_NAME):
        # 幂等：已存在不重复创建
        return

    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="tenant_id", dtype=DataType.INT64, is_partition_key=True),
        FieldSchema(name="doc_id", dtype=DataType.INT64),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="doc_filename", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]
    schema = CollectionSchema(fields, description="Knowledge base document chunks")
    collection = Collection(COLLECTION_NAME, schema)

    # 创建 IVF_FLAT 索引（nlist=128 适用于 10k+ 切片规模）
    # metric_type=IP：内积；bge-m3 向量已归一化，内积结果即余弦相似度，但省去归一化计算
    index_params = {
        "metric_type": "IP",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128},
    }
    collection.create_index("vector", index_params)
    # load 把索引载入内存，检索前必须 load
    collection.load()


def get_milvus_collection() -> Collection:
    """获取 knowledge_chunks Collection 实例（用于插入/检索）。"""
    _connect_milvus()
    return Collection(COLLECTION_NAME)


# ────────── 规格 009: 长期记忆 Collection ──────────
def init_memory_collection():
    """创建 user_memories Collection（如已存在则跳过）。

    流程:
      1. 连接 Milvus
      2. 已存在则跳过
      3. 定义 schema（含 user_id/fact_type/created_at）-> 创建 -> 建索引 -> load

    设计决策:
      与知识库 collection 分开，因为记忆的访问模式不同：按 tenant_id+user_id 隔离，
      且需要 fact_type 区分事实类型（偏好/身份/历史等），便于 save_memory 做先删后插的 upsert。
      created_at 用 DOUBLE 存时间戳，方便记忆有时效性时按时间过滤。

    Schema: id/tenant_id/user_id/fact_type/content/vector/created_at
    """
    _connect_milvus()

    if utility.has_collection(MEMORY_COLLECTION_NAME):
        # schema 兼容校验：旧版 user_id 为 INT64，#5 改 VARCHAR 后需 drop 重建
        col = Collection(MEMORY_COLLECTION_NAME)
        try:
            for f in col.schema.fields:
                if f.name == "user_id" and f.dtype != DataType.VARCHAR:
                    utility.drop_collection(MEMORY_COLLECTION_NAME)
                    break
            else:
                return  # schema 匹配，直接复用
        except Exception:
            return  # 校验失败不阻断（保持原幂等行为）

    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="tenant_id", dtype=DataType.INT64, is_partition_key=True),
        FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="fact_type", dtype=DataType.VARCHAR, max_length=50),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2048),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
        FieldSchema(name="created_at", dtype=DataType.DOUBLE),
    ]
    schema = CollectionSchema(fields, description="User long-term memories")
    collection = Collection(MEMORY_COLLECTION_NAME, schema)

    collection.create_index("vector", {
        "metric_type": "IP",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128},
    })
    collection.load()


def get_memory_collection() -> Collection:
    """获取 user_memories Collection 实例（用于长期记忆插入/检索）。"""
    _connect_milvus()
    return Collection(MEMORY_COLLECTION_NAME)


# ────────── ES Index 管理 ──────────
ES_INDEX_NAME = "knowledge_chunks"


def init_es_index():
    """创建 ES knowledge_chunks index（如已存在则跳过）。

    设计决策:
      chunk_text 用 ik_smart 中文分词器，适配中文知识库的 BM25 召回。
      is_latest 布尔字段用于文档版本管理：同一文档重新解析后，旧切片 is_latest=false，
      检索时只召回 is_latest=true 的切片，实现文档更新不中断检索。

    Mapping:
      - tenant_id: long
      - doc_id: long
      - chunk_index: integer
      - chunk_text: text (ik_smart 分词)
      - doc_filename: keyword
      - is_latest: boolean
    """
    from elasticsearch import Elasticsearch

    es = Elasticsearch(settings.elasticsearch_url)

    if es.indices.exists(index=ES_INDEX_NAME):
        return

    mapping = {
        "properties": {
            "tenant_id": {"type": "long"},
            "doc_id": {"type": "long"},
            "chunk_index": {"type": "integer"},
            "chunk_text": {"type": "text", "analyzer": "ik_smart"},
            "doc_filename": {"type": "keyword"},
            "is_latest": {"type": "boolean"},
        }
    }
    es.indices.create(index=ES_INDEX_NAME, mappings=mapping)


def get_es_client():
    """获取 Elasticsearch 客户端实例。"""
    from elasticsearch import Elasticsearch

    return Elasticsearch(settings.elasticsearch_url)
