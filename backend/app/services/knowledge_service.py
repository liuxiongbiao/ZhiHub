"""
backend/app/services/knowledge_service.py

职责: 文档上传前置校验、状态流转管理（待处理->处理中->已就绪/解析失败）、
      重复上传版本策略（旧版本归档且不再进检索库）、重建索引任务发布、存储上限校验

核心设计:
  知识库文档采用"版本归档 + 异步处理"模式：同名文件重新上传时，旧版本标记 is_latest=False
  并归档（不再进入检索库，避免命中过期内容），新版本 vN+1 投递给异步 Worker 做解析+分块+向量化。
  文档状态机: pending -> processing -> ready/failed，failed 可重建。

关键技术点:
  - 版本归档策略: 旧版本 is_latest=False，检索时只查 is_latest=True，保证检索结果始终最新
  - 异步任务投递: 上传接口只负责存文件+建记录，解析由 Worker 异步完成，接口快速返回
  - 文件落盘临时目录: Worker 从磁盘读文件做解析，解耦上传与解析的时序
  - 存储上限: 限制租户 is_latest 文档数，防止滥用

依赖关系:
  - 上游: app/api/v1/knowledge.py（上传/列表/重建端点）
  - 下游: app.crud.knowledge（持久化）、app.utils.file_parser（文件校验）、
          app.core.rabbitmq（异步任务投递）、app.models.knowledge_document（ORM）
"""

import logging
import os

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.rabbitmq import publish_knowledge_task
from app.config import settings
from app.crud import knowledge as knowledge_crud
from app.utils.file_parser import FileValidationError, validate_file

logger = logging.getLogger(__name__)

# 允许上传的 Content-Type 白名单
# —— application/octet-stream 兜底: 部分浏览器对 .md 文件发送此通用类型
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/markdown",
    "text/plain",
    "application/octet-stream",  # some browsers send this for .md files
}


class KnowledgeError(Exception):
    """知识库业务异常基类."""


class UnsupportedFormat(KnowledgeError):
    """不支持的格式 -> 400."""


class FileTooLarge(KnowledgeError):
    """文件过大 -> 400."""


class EmptyFile(KnowledgeError):
    """空文件 -> 400."""


class DocumentNotFound(KnowledgeError):
    """文档不存在 -> 404."""


class DocumentProcessing(KnowledgeError):
    """文档正在处理中 -> 409."""


class KnowledgeLimitExceeded(KnowledgeError):
    """知识库数量超套餐上限 -> 403."""


# 套餐知识库数量上限（PRD §3.2）: trial 1 / basic 3 / pro 及以上无限
PLAN_KB_LIMITS: dict[str, int | None] = {
    "trial": 1,
    "basic": 3,
    "pro": None,        # None = 无限
    "enterprise": None,
}


def check_kb_count(db: Session, tenant_id: int) -> dict:
    """检查租户知识库（文档）数量是否超套餐上限。

    按租户 plan_type 查上限表：trial 1 / basic 3 / pro 及以上无限。
    只统计 is_latest=True 的文档（旧版本归档不占配额，与 check_storage_limit 口径一致）。

    Args:
        db: 数据库会话
        tenant_id: 租户 ID

    Returns:
        dict: plan_type / limit(None=无限) / current / allowed(是否允许再上传)
    """
    from app.crud import tenant as tenant_crud
    from app.models.knowledge_document import KnowledgeDocument
    from sqlalchemy import select, func

    tenant = tenant_crud.get_by_id(db, tenant_id)
    plan_type = tenant.plan_type if tenant else "trial"
    limit = PLAN_KB_LIMITS.get(plan_type, 1)

    current = db.execute(
        select(func.count()).select_from(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.is_latest == True,  # noqa: E712
        )
    ).scalar() or 0

    allowed = True if limit is None else current < limit
    return {"plan_type": plan_type, "limit": limit, "current": current, "allowed": allowed}


def upload(db: Session, *, file: UploadFile, tenant_id: int, expires_days: int | None = None) -> dict:
    """上传文档：校验 -> 创建记录 -> 投递异步任务。

    流程:
      1. 读取文件内容到内存
      2. 调用 validate_file 校验格式/大小/空文件，映射为对应业务异常
      3. 版本管理: 同名文件旧版本标记 is_latest=False，新版本号 +1
      4. 创建文档记录（status=pending）
      5. 保存文件到临时目录供异步 Worker 读取
      6. 投递异步解析任务到 RabbitMQ

    设计决策:
      - 文件全量读入内存: demo 简化；生产应流式上传到对象存储
      - 旧版本归档不删除: 保留历史版本可追溯，但检索时只取 is_latest
      - 临时目录落盘: Worker 与 API 可能不在同机，生产应改用共享存储/对象存储

    Args:
        db: 数据库会话
        file: FastAPI UploadFile 对象
        tenant_id: 租户 ID

    Returns:
        dict: doc_id / filename / file_type / file_size / status / version / is_latest / uploaded_at
    """
    # 1. 读取文件内容 —— 全量读入内存，demo 简化
    content = file.file.read()
    filename = file.filename or "unknown"

    # 套餐知识库数量上限校验（trial 1 / basic 3 / pro 无限），超限 → 403
    kb_check = check_kb_count(db, tenant_id)
    if not kb_check["allowed"]:
        raise KnowledgeLimitExceeded(
            f"知识库数量已达套餐上限: {kb_check['current']}/{kb_check['limit']}（{kb_check['plan_type']} 套餐）"
        )

    # 存储配额校验（按套餐 MB，计入本次上传大小）
    if not check_storage_limit(db, tenant_id, incoming_bytes=len(content)):
        raise KnowledgeLimitExceeded("知识库存储空间已满，请升级套餐或删除部分旧文档")

    # 2. 校验 —— 映射底层 FileValidationError 为具体业务异常，便于 API 层返回精确状态码
    try:
        validate_file(filename, content)
    except FileValidationError as e:
        msg = str(e)
        if "格式" in msg:
            raise UnsupportedFormat(msg) from e
        elif "大小" in msg:
            raise FileTooLarge(msg) from e
        elif "空" in msg:
            raise EmptyFile(msg) from e
        raise KnowledgeError(msg) from e

    # 提取扩展名作为 file_type
    file_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"
    file_size = len(content)

    # 3. 版本管理：旧同名文件归档 —— is_latest=False，使其不再进入检索库
    existing = knowledge_crud.get_latest_by_filename(db, tenant_id, filename)
    new_version = 1
    if existing:
        # 旧版本标记为非最新 —— 检索时只查 is_latest=True，保证命中最新内容
        knowledge_crud.mark_not_latest(db, existing)
        new_version = existing.version + 1

    # 4. 创建记录 —— status=pending，等待 Worker 处理后改为 ready/failed
    # PRD §3.2 文档生命周期：未指定有效期时使用系统默认（doc_validity_days=90）；
    # expires_days=0 表示永久（expires_at=None）
    from datetime import datetime, timedelta, timezone
    from app.config import settings

    expires_at = None
    if expires_days is None:
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            days=settings.doc_validity_days
        )
    elif expires_days > 0:
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expires_days)

    doc = knowledge_crud.create(
        db,
        tenant_id=tenant_id,
        filename=filename,
        file_type=file_type,
        file_size=file_size,
        version=new_version,
        expires_at=expires_at,
    )
    db.commit()

    # 5. 保存文件到本地磁盘（供异步 Worker 读取）
    # —— 临时目录落盘，生产应替换为对象存储
    os.makedirs(settings.upload_storage_dir, exist_ok=True)
    upload_path = os.path.join(
        settings.upload_storage_dir,
        f"{doc.id}_{os.path.basename(filename)}",
    )
    with open(upload_path, "wb") as f:
        f.write(content)

    # 6. 投递异步任务 —— 上传接口到此返回，解析由 Worker 异步完成
    publish_knowledge_task(doc.id, tenant_id)

    return {
        "doc_id": doc.id,
        "filename": doc.filename,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "status": doc.status,
        "version": doc.version,
        "is_latest": doc.is_latest,
        "uploaded_at": doc.created_at,
        "expires_at": doc.expires_at,
    }


def rebuild(db: Session, *, doc_id: int, tenant_id: int) -> dict:
    """重建文档索引：重置为 pending + 重新投递任务。

    仅 failed 和 ready 状态的文档可重建。

    流程:
      1. 加载文档，校验存在性
      2. processing 状态不可重建（防止重复投递），抛 409
      3. 状态重置为 pending，chunk_count 清零
      4. 重新投递异步任务

    设计决策:
      - processing 状态拒绝重建: 避免同一文档被并发解析，产生重复分块
      - chunk_count 清零: 重建前先清空旧分块计数，避免展示脏数据

    Args:
        db: 数据库会话
        doc_id: 文档 ID
        tenant_id: 租户 ID

    Returns:
        dict: doc_id / status / message
    """
    doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)
    if doc is None:
        raise DocumentNotFound(f"文档不存在: {doc_id}")
    if doc.status == "processing":
        # processing 不可重建 —— 防止并发解析产生重复分块
        raise DocumentProcessing(f"文档正在处理中: {doc_id}")

    knowledge_crud.update_status(db, doc, "pending")
    knowledge_crud.set_chunk_count(db, doc, 0)
    db.commit()

    # 重新投递异步任务 —— Worker 会重新解析+分块+向量化
    publish_knowledge_task(doc.id, tenant_id)

    return {"doc_id": doc.id, "status": "pending", "message": "重建索引任务已提交"}


def delete_document(db: Session, *, doc_id: int, tenant_id: int) -> dict:
    """删除文档（含检索库切片清理）：仅租户内可删，幂等。"""
    doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)
    if doc is None:
        raise DocumentNotFound(f"文档不存在: {doc_id}")

    # 先清检索库切片（Milvus + ES），失败仅告警不阻断删除主记录
    try:
        from app.core.embedding_client import get_es_client, get_milvus_collection
        collection = get_milvus_collection()
        collection.delete(expr=f"doc_id == {doc_id}")
        es = get_es_client()
        es.delete_by_query(
            index="knowledge_chunks",
            body={"query": {"term": {"doc_id": doc_id}}},
            refresh=True,
        )
    except Exception as e:
        logger.warning(f"Delete doc chunks failed (doc={doc_id}, will be cleaned by index maintenance): {e}")

    db.delete(doc)
    db.commit()
    return {"doc_id": doc_id, "deleted": True}


def list_documents(
    db: Session,
    tenant_id: int,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """文档列表（分页+筛选）.

    Args:
        db: 数据库会话
        tenant_id: 租户 ID
        status: 可选状态筛选
        page: 页码，从 1 开始
        page_size: 每页条数

    Returns:
        dict: items(文档详情列表) / total / page / page_size
    """
    items, total = knowledge_crud.get_by_tenant(
        db, tenant_id=tenant_id, status=status, page=page, page_size=page_size
    )
    return {
        "items": [
            {
                "doc_id": d.id,
                "filename": d.filename,
                "file_type": d.file_type,
                "file_size": d.file_size,
                "status": d.status,
                "version": d.version,
                "is_latest": d.is_latest,
                "chunk_count": d.chunk_count,
                "uploaded_at": d.created_at,
                "expires_at": d.expires_at,
                "quality_status": d.quality_status,
            }
            for d in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# PRD §3.2 存储上限校验（：按套餐 MB 配额，基于 file_size 汇总）
def check_storage_limit(db, tenant_id: int, incoming_bytes: int = 0) -> bool:
    """检查租户知识库存储是否超限（配额 0=无限制；只统计 is_latest 文档）。

    Args:
        db: 数据库会话
        tenant_id: 租户 ID

    Returns:
        bool: True 未超限（允许上传），False 超限（拒绝）
    """
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.tenant import Tenant
    from app.config import settings
    from sqlalchemy import select, func
    import json as _json

    tenant = db.get(Tenant, tenant_id)
    plan_type = tenant.plan_type if tenant else "trial"
    try:
        quotas = _json.loads(settings.storage_quota_mb) if settings.storage_quota_mb else {}
    except Exception:
        quotas = {}
    quota_mb = quotas.get(plan_type, 500)
    if quota_mb <= 0:
        return True

    used = db.execute(
        select(func.coalesce(func.sum(KnowledgeDocument.file_size), 0)).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.is_latest == True,  # noqa: E712
        )
    ).scalar() or 0
    return used + incoming_bytes < quota_mb * 1024 * 1024
