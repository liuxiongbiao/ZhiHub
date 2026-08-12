"""
backend/app/crud/knowledge.py

职责: knowledge_documents 表的数据库访问层（文档元数据 CRUD、版本管理查询）

核心设计:
  所有查询强制 tenant_id 过滤（多租户隔离）；版本管理通过 is_latest 标记只检索最新版本。
  只 flush 不 commit，事务由 Service 层控制。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge_document import KnowledgeDocument


def get_by_id(db: Session, doc_id: int, tenant_id: int) -> KnowledgeDocument | None:
    """按文档 ID + 租户 ID 查询（租户隔离）."""
    return db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()


def get_latest_by_filename(db: Session, tenant_id: int, filename: str) -> KnowledgeDocument | None:
    """查询租户下同名文件的最新版本（用于版本管理）."""
    return db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.filename == filename,
            KnowledgeDocument.is_latest.is_(True),
        )
    ).scalar_one_or_none()


def get_by_tenant(
    db: Session,
    tenant_id: int,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[KnowledgeDocument], int]:
    """分页查询租户下的文档列表，可选按状态筛选。返回 (items, total)."""
    query = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)

    if status:
        query = query.where(KnowledgeDocument.status == status)

    # 总数
    total = _count(db, tenant_id, status)

    # 分页
    items = db.execute(
        query.order_by(KnowledgeDocument.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    return list(items), total


def _count(db: Session, tenant_id: int, status: str | None = None) -> int:
    """统计租户下文档总数."""
    from sqlalchemy import func as sqlfunc

    conds = [KnowledgeDocument.tenant_id == tenant_id]
    if status:
        conds.append(KnowledgeDocument.status == status)
    result = db.execute(
        select(sqlfunc.count()).select_from(KnowledgeDocument).where(*conds)
    ).scalar()
    return result if result else 0


def create(
    db: Session, *, tenant_id: int, filename: str, file_type: str, file_size: int, version: int
) -> KnowledgeDocument:
    """创建文档记录."""
    doc = KnowledgeDocument(
        tenant_id=tenant_id,
        filename=filename,
        file_type=file_type,
        file_size=file_size,
        status="pending",
        version=version,
        is_latest=True,
    )
    db.add(doc)
    db.flush()
    return doc


def update_status(
    db: Session, doc: KnowledgeDocument, status: str, *, error_message: str | None = None
) -> None:
    """更新文档处理状态."""
    doc.status = status
    if error_message:
        doc.error_message = error_message
    if status == "ready":
        doc.error_message = None  # 清除旧错误
    db.flush()


def mark_not_latest(db: Session, doc: KnowledgeDocument) -> None:
    """将文档标记为非最新版本."""
    doc.is_latest = False
    db.flush()


def set_chunk_count(db: Session, doc: KnowledgeDocument, count: int) -> None:
    """设置文档切片总数."""
    doc.chunk_count = count
    db.flush()
