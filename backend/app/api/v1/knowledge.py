"""
backend/app/api/v1/knowledge.py

职责: 知识库文档上传、列表查询、重建索引、检索测试台

端点概览:
  POST /upload              上传文档 -> knowledge_service.upload -> 投递 RabbitMQ 异步处理
  GET  /list                文档列表（分页 + 状态筛选）-> knowledge_service.list_documents
  POST /{doc_id}/rebuild    重建索引（202 Accepted）-> knowledge_service.rebuild
  POST /test                检索测试台: 纯向量 vs 混合检索 -> retrieval_service.hybrid_search

异常映射: UnsupportedFormat/FileTooLarge/EmptyFile -> 400; DocumentNotFound -> 404; DocumentProcessing -> 409

依赖关系:
  - 上游: main.py 挂载 prefix=/api/v1/knowledge
  - 下游: app.services.knowledge_service、app.services.retrieval_service
"""

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.schemas.knowledge import (
    DocumentListResponse,
    DocumentUploadResponse,
    RebuildResponse,
    SearchTestRequest,
    SearchTestResponse,
)
from app.services import knowledge_service

router = APIRouter()


# ---------- 异常 -> HTTP 状态码映射 ----------
def _handle_knowledge_error(e: Exception) -> HTTPException:
    """将 KnowledgeError 子类映射到对应 HTTP 状态码，未知异常 -> 500。"""
    mapping: dict[type, tuple[int, str]] = {
        knowledge_service.UnsupportedFormat: (400, str(e)),
        knowledge_service.FileTooLarge: (400, str(e)),
        knowledge_service.EmptyFile: (400, str(e)),
        knowledge_service.DocumentNotFound: (404, str(e)),
        knowledge_service.DocumentProcessing: (409, str(e)),
        knowledge_service.KnowledgeLimitExceeded: (403, str(e)),
    }
    for exc_cls, (code, detail) in mapping.items():
        if isinstance(e, exc_cls):
            return HTTPException(status_code=code, detail=detail)
    return HTTPException(status_code=500, detail="服务器内部错误")


def _get_tenant_id(request: Request) -> int:
    """从 request.state 提取 tenant_id，未认证 -> 401。"""
    tid = getattr(request.state, "tenant_id", None)
    if tid is None:
        raise HTTPException(status_code=401, detail="未认证")
    return tid


# ---------- POST /upload ----------
@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_role("admin")),
):
    """上传知识库文档: 调 knowledge_service.upload。"""
    tenant_id = _get_tenant_id(request)
    try:
        result = knowledge_service.upload(db, file=file, tenant_id=tenant_id)
    except knowledge_service.KnowledgeError as e:
        raise _handle_knowledge_error(e)
    return result


# ---------- GET /list ----------
@router.get("/list", response_model=DocumentListResponse)
def list_documents(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """文档列表（分页 + 状态筛选）: 调 knowledge_service.list_documents。"""
    tenant_id = _get_tenant_id(request)
    return knowledge_service.list_documents(
        db, tenant_id=tenant_id, status=status_filter, page=page, page_size=page_size
    )


# ---------- POST /{doc_id}/rebuild ----------
@router.post("/{doc_id}/rebuild", response_model=RebuildResponse, status_code=202)
def rebuild_document(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_role("admin")),
):
    """重建文档索引（202 Accepted）: 调 knowledge_service.rebuild。"""
    tenant_id = _get_tenant_id(request)
    try:
        return knowledge_service.rebuild(db, doc_id=doc_id, tenant_id=tenant_id)
    except knowledge_service.KnowledgeError as e:
        raise _handle_knowledge_error(e)


# ---------- POST /test ----------
@router.post("/test", response_model=SearchTestResponse)
def search_test(
    body: SearchTestRequest,
    request: Request,
):
    """检索测试台: 纯向量 vs 混合检索对比，调 retrieval_service.hybrid_search。"""
    tenant_id = _get_tenant_id(request)

    if not body.query.strip():
        raise HTTPException(status_code=400, detail="查询不能为空")

    from app.services.retrieval_service import hybrid_search

    return hybrid_search(query=body.query, tenant_id=tenant_id, history=body.history)
