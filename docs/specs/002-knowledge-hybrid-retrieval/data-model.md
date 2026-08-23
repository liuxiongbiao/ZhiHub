# Data Model: 知识库上传 + 混合检索引擎

**Phase 1 Output** | **Date**: 2026-08-04

## Entity: KnowledgeDocument (MySQL)

知识库文档的元数据记录，存储于 MySQL。

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | BIGINT | PK, AUTO_INCREMENT | 主键 |
| `tenant_id` | BIGINT | NOT NULL, INDEX | 所属租户（多租户隔离） |
| `filename` | VARCHAR(500) | NOT NULL | 原始文件名 |
| `file_type` | VARCHAR(20) | NOT NULL | 文件格式：pdf / docx / md |
| `file_size` | INTEGER | NOT NULL | 文件大小（bytes） |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'pending' | 处理状态：pending / processing / ready / failed |
| `error_message` | TEXT | NULLABLE | 失败原因（status=failed 时填入） |
| `version` | INTEGER | NOT NULL, DEFAULT 1 | 版本号，同文件名每次上传 +1 |
| `is_latest` | BOOLEAN | NOT NULL, DEFAULT TRUE | 是否最新版本（旧版本为 FALSE，不进检索库） |
| `chunk_count` | INTEGER | NOT NULL, DEFAULT 0 | 切片总数（处理完成后填充） |
| `created_at` | DATETIME | NOT NULL | 上传时间（继承 BaseModel） |
| `updated_at` | DATETIME | NOT NULL | 最后更新时间（继承 BaseModel） |

**State Machine**:
```
pending ──→ processing ──→ ready
  │                         │
  └────→ failed ←───────────┘
            │
            └── (rebuild) ──→ pending (version 不变, is_latest 不变)
```

**Version 规则**:
- 同名文件上传时，先查当前租户下同名且 `is_latest=true` 的文档，将其 `is_latest` 置为 `false`。
- 新记录 `version = 旧版本.version + 1`, `is_latest = true`。
- 重建索引（rebuild）不改变版本号——它是同一版本文档的重新处理。

## Entity: DocumentChunk (Milvus + ES)

文档切片数据同时存在于 Milvus 和 Elasticsearch（双写）。两个存储共用以下逻辑字段：

| Field | Milvus Type | ES Type | Description |
|---|---|---|---|
| `id` / `_id` | INT64 (auto_id) | auto-generated | 切片唯一标识 |
| `tenant_id` | INT64 (partition_key) | long | 租户隔离键 |
| `doc_id` | INT64 | long | 来源文档 ID |
| `chunk_index` | INT64 | integer | 切片在文档内的序号（从 0 开始） |
| `chunk_text` | VARCHAR(4096) | text (ik_smart) | 切片文本内容 |
| `vector` | FLOAT_VECTOR(1024) | — (Milvus only) | bge-m3 embedding 向量 |
| `doc_filename` | — | keyword | 来源文件名（ES only，方便调试） |
| `is_latest` | — | boolean | 是否最新版本（ES only，检索时过滤旧版本） |

**双写一致性**: Worker 先写 Milvus → 再写 ES → 任一侧失败则重试失败侧（最多 3 次）→ 全部失败标记文档 failed。

**检索时过滤**: Milvus 侧通过 partition key (tenant_id) 过滤；ES 侧通过 `tenant_id` + `is_latest=true` 过滤。旧版本切片和 ES 中漏设过滤的旧数据都不会出现在结果中。

## DTO: RetrievalResult

检索操作的结果结构（非持久化实体，API 响应中返回）。

```python
@dataclass
class ChunkResult:
    doc_id: int
    doc_filename: str
    chunk_index: int
    chunk_text: str
    score: float          # reranker relevance_score 或向量相似度
    source: str           # "vector" | "bm25" | "hybrid"

@dataclass
class RetrievalResult:
    query: str
    rewritten_query: str | None
    vector_results: list[ChunkResult]     # 纯向量召回 Top5
    hybrid_results: list[ChunkResult]     # 混合检索 Top5
    hyde_fallback: bool                   # 是否触发了 HyDE 兜底
    empty_knowledge: bool                 # 是否知识库为空
    elapsed_ms: int                       # 总耗时（毫秒）
```

## Database Migrations

需创建 Alembic migration 文件，新增 `knowledge_documents` 表。现有 `users` / `tenants` / `tenant_members` 表不变。
