"""
backend/app/schemas/knowledge.py

职责: 文档上传响应、检索测试请求/响应、文档列表 Pydantic 模型
对应日程: 002-knowledge-rag
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ---------- 文档上传 ----------
class DocumentUploadResponse(BaseModel):
    """文档上传响应：元数据 + 处理状态 + 版本号。"""
    doc_id: int
    filename: str
    file_type: str
    file_size: int
    status: str
    version: int
    is_latest: bool
    uploaded_at: datetime


# ---------- 文档列表 ----------
class DocumentItem(BaseModel):
    """文档列表项：元数据 + 切片数 + 版本/最新标记。"""
    doc_id: int
    filename: str
    file_type: str
    file_size: int
    status: str
    version: int
    is_latest: bool
    chunk_count: int
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    """文档列表分页响应。"""
    items: list[DocumentItem]
    total: int
    page: int
    page_size: int


# ---------- 检索测试 ----------
class SearchTestRequest(BaseModel):
    """检索测试请求：查询语句 + 历史对话上下文。"""
    query: str = Field(..., min_length=1, max_length=500)
    history: list[dict] = Field(default_factory=list)


class ChunkResult(BaseModel):
    """检索命中的切片结果：文档、切片序号、文本、相似度分数、检索来源。"""
    doc_id: int
    doc_filename: str
    chunk_index: int
    chunk_text: str
    score: float
    source: str  # "vector" | "bm25" | "hybrid"


class SearchTestResponse(BaseModel):
    """检索测试响应：向量/混合检索结果 + HyDE 回退/空库标记 + 耗时。"""
    query: str
    rewritten_query: str | None
    vector_results: list[ChunkResult]
    hybrid_results: list[ChunkResult]
    hyde_fallback: bool
    empty_knowledge: bool
    elapsed_ms: int


# ---------- 重建索引 ----------
class RebuildResponse(BaseModel):
    """重建索引响应：文档 ID、状态、提示消息。"""
    doc_id: int
    status: str
    message: str
