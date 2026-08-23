# Research: 知识库上传 + 混合检索引擎

**Phase 0 Output** | **Date**: 2026-08-04

## R1: Milvus Collection Schema 设计

**Decision**: Collection 名为 `knowledge_chunks`，使用 partition key（基于 tenant_id），索引类型 IVF_FLAT，metric_type=IP（内积相似度）。

**Rationale**:
- bge-m3 输出的 embedding 已做 L2 归一化，内积等价于余弦相似度，计算效率更高。
- IVF_FLAT 在 10k 切片规模下查询延迟 <10ms，无需 IVF_PQ 等更复杂的压缩索引。
- Partition key 按 tenant_id 分区，租户级别的数据隔离在 Milvus 层也得到保障（双重保险）。

**Schema**:
```python
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="tenant_id", dtype=DataType.INT64, is_partition_key=True),
    FieldSchema(name="doc_id", dtype=DataType.INT64),
    FieldSchema(name="chunk_index", dtype=DataType.INT64),
    FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=4096),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),
]
```

**Alternatives considered**:
- COSINE 距离类型：bge-m3 归一化后与 IP 等价，且 Milvus IP 计算比 COSINE 快 ~5%。
- HNSW 索引：demo 阶段 10k 数据量 IVF_FLAT 足够，HNSW 构建时间更长且内存占用更大。

---

## R2: Elasticsearch Index Mapping

**Decision**: Index 名为 `knowledge_chunks`，text 字段使用 `ik_smart` 分词器，不做同义词扩展。

**Rationale**:
- `ik_smart` 粗粒度分词对短查询友好，避免 `ik_max_word` 产生过多无意义词项导致 BM25 评分稀释。
- demo 阶段不引入同义词词典维护成本（Day 10 缓冲期可加）。

**Mapping**:
```json
{
  "mappings": {
    "properties": {
      "tenant_id": {"type": "long"},
      "doc_id": {"type": "long"},
      "chunk_index": {"type": "integer"},
      "chunk_text": {"type": "text", "analyzer": "ik_smart"},
      "doc_filename": {"type": "keyword"}
    }
  }
}
```

**Alternatives considered**:
- `ik_max_word`：召回更多但噪音大，混合检索场景下由向量召回补充语义覆盖，不需要过度分词。
- 使用 ES 的 `dense_vector` 字段（v8.0+）：仍需要 Milvus 做向量主引擎（生产级近似搜索更成熟），且 demo 阶段不引入 ES 的 kNN 造成复杂度过高。

---

## R3: RRF 融合参数 k 的选择

**Decision**: k=60（RRF 标准推荐值），不做自适应调整。

**Rationale**:
- 学术论文和工业实践均证实 k=60 是鲁棒的默认值，对召回列表大小不敏感。
- 自适应调参（如根据两路召回大小动态调 k）增加复杂性但收益有限。
- RRF 公式：`score(d) = Σ 1/(k + rank_i(d))`，两路召回融合时 rank 来自向量和 BM25 的排序位置。

**Alternatives considered**:
- 加权分数融合（向量分数 × α + BM25 分数 × (1-α)）：需要归一化分数，而向量相似度（IP 0~1）和 BM25 分数（无上界）的分布差异大，归一化方案（min-max / softmax）都引入了额外变量。RRF 只依赖排序位置，天然免疫分数分布差异。

---

## R4: Chunk 切分策略

**Decision**: 按段落 + 标题规则切片，chunk_size=512 tokens，chunk_overlap=128 tokens。

**Rationale**:
- 512 tokens 是 bge-m3 推荐的输入长度，覆盖中文约 300-400 字，足够表达一个完整段落。
- 128 token overlap 保持上下文连贯性，避免关键信息落在切片边界被截断。
- 按段落（`\n\n`）和标题（以 `#` 开头的行）自然边界切分，优于固定长度切割（后者可能切碎语义单元）。

**实现细节**:
- Markdown: 按 `##` 标题分段，标题内容作为该段的上下文前缀。
- PDF/Word: 先提取纯文本，按双换行分段，再按 512 token 限制做二级切分。
- 每个 chunk 记录 `chunk_index`（在文档内的序号），检索结果中用于定位原始位置。

**Alternatives considered**:
- RecursiveCharacterTextSplitter (LangChain)：方便但引入了 LangChain 依赖，且本项目只用到它的文本切分功能（杀鸡用牛刀）。自定义规则切分更轻量且完全可控。
- RagFlow 知识库引擎：PRD 已明确砍掉，用云端 embedding API 替代。

---

## R5: HyDE 兜底策略的触发条件

**Decision**: 当 RRF+Rerank 后的 Top1 分数 < 0.6（reranker relevance_score 阈值）时触发 HyDE。

**Rationale**:
- bge-reranker-v2-m3 的 relevance_score 范围 0-1，0.6 是一个经验阈值——低于此值表示模型认为内容和查询几乎无关。
- HyDE 流程：用 LLM 生成一份假设文档 → 对假设文档做 embedding → 用这个向量去 Milvus 做二次检索 → 返回结果并标记 `hyde_fallback=true`。
- 只重试一次，不递归（防止无限循环）。

**Alternatives considered**:
- 用向量相似度（IP score）做阈值判断：向量相似度受 embedding 模型偏差影响，不同领域的相似度分布不同。Reranker 是专门训练用于相关性判断的，更可靠。
- 无条件 HyDE 每次检索都生成假设文档：延迟翻倍（多一次 LLM 调用），性价比低。

---

## R6: Embedding API 调用策略

**Decision**: 批量调用硅基流动 API（batch_size=16），失败重试 3 次（指数退避 1s/2s/4s），单切片失败不阻塞同批次其他切片。

**Rationale**:
- 硅基流动 bge-m3 API 支持批量输入（多 texts），batch_size=16 在单次调用延迟和批量效率间折中。
- 指数退避符合标准 API 重试最佳实践。
- 单切片失败不阻塞（"fail partial" 策略）：避免一个坏切片导致整份文档处理失败。

**API Endpoint**: `POST https://api.siliconflow.cn/v1/embeddings`
```json
{
  "model": "BAAI/bge-m3",
  "input": ["chunk1 text", "chunk2 text", ...],
  "encoding_format": "float"
}
```

---

## R7: 查询改写策略

**Decision**: 用 LLM（DeepSeek API）做指代补全，将对话历史中最近 3 轮问答 + 当前查询组合成 prompt，输出改写后的完整查询。

**Rationale**:
- 用户可能输入省略/指代查询（如"这个怎么改"），不做改写直接 embedding 会丢失上下文。
- 仅做指代补全（不扩写/不生成子问题），保持简单可控。
- 实现上：用 litellm 调用 DeepSeek，fallback 不需要 LLM 则直接返回原始查询。

**Prompt 模板**（存入 `agents/prompts/query_rewrite.txt`）:
```
根据对话历史，将用户的当前查询改写为独立、完整的检索查询。
只补全指代和省略，不添加额外信息。如果查询已经完整，直接返回原文。

对话历史：
{history}

当前查询：{query}

改写后的查询：
```

---

## R8: Celery Queue 设计

**Decision**: 单队列 `knowledge_processing`，无优先级，并发 worker 数=2。

**Rationale**:
- Day 3 demo 阶段文档量不大，单队列 + 2 worker 足够。
- 任务幂等设计：Worker 拉取任务后通过 `SELECT ... FOR UPDATE` 锁定文档行，防止重复处理。
- 消息格式：
```json
{
  "task": "process_document",
  "doc_id": 123,
  "tenant_id": 5
}
```

**Alternatives considered**:
- 多队列按优先级拆分：demo 阶段无此需求，过度设计。
