"""
backend/app/tasks/knowledge_tasks.py

职责: 消费 RabbitMQ 队列 -> 文档解析(规则切片，非 RagFlow) -> 调用云端 embedding API 向量化
      -> 并行写入 Milvus + ES -> 双写不一致重试 -> 更新文档状态

核心设计:
  文档异步处理流水线: 上传 API 发布 MQ 消息 -> worker 消费 -> 解析切片 -> embedding -> 双写。
  Milvus 存向量（语义检索）、ES 存全文（关键词检索），双写保证两种检索都能命中。

关键技术点:
  - 双写一致性: Milvus 和 ES 独立写入，任一失败需重试。
    重试时先删旧数据再写（幂等），避免重复切片。
    全部重试失败抛异常，文档标记 failed。
    注意: 当前是"尽力一致"而非"强一致"--Milvus 成功但 ES 失败时，
    Milvus 已有数据但 ES 没有，语义检索可命中但关键词检索缺失。
    生产环境应用 Outbox 模式或分布式事务保证最终一致。
  - check_quality 乱码率检测: 统计控制字符（< 32 且非 \\n\\r\\t）占比，
    超 0.3 标记 pending_review 不进检索库。
  - 版本管理: 写入前先删该文档的旧切片（Milvus delete + ES delete_by_query），
    支持文档 rebuild 时清理旧版本。
  - 临时文件: 上传时写入 tempfile，处理完删除。文件内容不存数据库（demo 简化）。

依赖关系:
  - 上游: app/core/rabbitmq.py publish_knowledge_task（Celery + RabbitMQ 消息驱动）
  - 下游: app.core.embedding_client（embedding/ES/Milvus）、app.crud.knowledge、
          app.utils.file_parser（解析切片）
"""

import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from celery import shared_task
from app.config import settings
from app.core.database import SessionLocal
from app.core.embedding_client import (
    embed_batches,
    get_es_client,
    get_milvus_collection,
    init_es_index,
    init_milvus_collection,
)
from app.crud import knowledge as knowledge_crud
from app.utils.file_parser import parse_and_chunk

logger = logging.getLogger(__name__)

# 双写重试次数（Milvus + ES 任一失败时重试）
MAX_DUAL_WRITE_RETRIES = 3
ES_BULK_CHUNK_SIZE = 500


@shared_task(name="app.tasks.knowledge_tasks.process_document")
def process_document(doc_id: int, tenant_id: int) -> None:
    """文档处理全流程：解析 -> 切片 -> embedding -> 双写 Milvus+ES -> 更新状态。

    流程:
      1. 载入文档记录，更新状态为 processing
      2. 初始化 Milvus collection + ES index（首次上传时）
      3. 从本地磁盘读取临时文件内容
      4. 解析 + 切片（规则切片，非 RagFlow）
      5. 批量 embedding 向量化
      6. 双写 Milvus + ES（重试 3 次）
      7. 更新文档状态为 ready，清理临时文件

    设计决策:
      - 文件内容不在数据库存储: demo 简化，上传时写临时文件，处理完删除。
        生产环境应用对象存储（S3/OSS）持久化原始文件。
      - 异常处理: 任何步骤失败都标记文档 failed，记录 error_message（截断 1000 字符）。
      - 临时文件不存在时降级为 ready + 0 chunks: 避免因文件丢失阻塞队列消费。

    Args:
        doc_id: 文档 ID
        tenant_id: 租户 ID
    """
    db = SessionLocal()
    try:
        doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)
        if doc is None:
            logger.error(f"Document not found: doc_id={doc_id}, tenant_id={tenant_id}")
            return
        # 仅处理 pending/processing 文档。demo reset 会把旧租户文档置 failed，
        # 已在队列中的旧任务据此跳过，防止 reset 后向旧租户重写向量。
        if doc.status not in ("pending", "processing"):
            logger.info(f"Document {doc_id} status={doc.status}, skip processing")
            return

        # 更新状态为 processing（标记开始处理，前端可显示进度）
        knowledge_crud.update_status(db, doc, "processing")
        db.commit()

        upload_path = os.path.join(
            settings.upload_storage_dir,
            f"{doc_id}_{os.path.basename(doc.filename)}",
        )
        legacy_upload_path = os.path.join(
            tempfile.gettempdir(),
            "zhihub_uploads",
            f"{doc_id}_{os.path.basename(doc.filename)}",
        )
        # 滚动升级兼容：处理部署前已经入队、仍落在旧临时目录中的真实文件。
        if not os.path.isfile(upload_path) and os.path.isfile(legacy_upload_path):
            upload_path = legacy_upload_path
        if not os.path.isfile(upload_path):
            reason = f"上传源文件不存在或 worker 无法访问共享目录: {upload_path}"
            logger.error(reason)
            knowledge_crud.update_status(db, doc, "failed", error_message=reason[:1000])
            knowledge_crud.set_chunk_count(db, doc, 0)
            db.commit()
            return

        # 确保检索索引存在。关闭 embedding 时只初始化 ES，避免误触向量链路。
        try:
            if settings.embedding_enabled:
                init_milvus_collection()
            init_es_index()
        except Exception as e:
            logger.warning(f"Init vector store failed (non-blocking): {e}")

        with open(upload_path, "rb") as f:
            content = f.read()

        # 1. 解析 + 切片（PRD §3.2：配置 RagFlow 走语义切片，否则/失败降级本地规则解析）
        chunks = None
        from app.core.ragflow_client import get_ragflow_client

        ragflow = get_ragflow_client()
        if not ragflow.is_mock:
            try:
                chunks = ragflow.parse(doc.filename, content)
            except Exception as e:
                logger.warning(f"RagFlow parse failed (doc={doc_id}), fallback local: {e}")
        if chunks is None:
            chunks = parse_and_chunk(doc.filename, content)
        if not chunks:
            knowledge_crud.update_status(db, doc, "failed", error_message="文档解析后无有效文本内容")
            db.commit()
            return

        # 2. 批量 Embedding
        chunk_texts = [c.text for c in chunks]

        # 乱码率/空切片检测（PRD §3.2）：超阈值标记 pending_review 不进检索库
        quality_rate, empty_ratio = check_quality(chunk_texts)
        if quality_rate > 0.3 or empty_ratio > 0.2:
            doc.quality_status = "pending_review"
            doc.status = "ready"
            doc.error_message = (
                f"质量检测未通过（乱码率 {quality_rate:.2f}，空切片占比 {empty_ratio:.2f}），待人工复核"
            )
            db.commit()
            logger.warning(
                f"Document {doc_id} pending_review: garbage {quality_rate:.2f}, empty {empty_ratio:.2f}"
            )
            return

        vectors: list[list[float]] | None = None
        if settings.embedding_enabled:
            vectors = embed_batches(chunk_texts)
            if len(vectors) != len(chunks):
                knowledge_crud.update_status(db, doc, "failed", error_message="Embedding向量数与切片数不匹配")
                db.commit()
                return
        else:
            logger.info("Embedding disabled: document %s will be indexed in ES only", doc_id)

        # 3. 双写 Milvus + ES（重试 3 次）
        # 并发窗口防护：写入前重查文档与租户仍可处理。worker 从通过第一次
        # 状态检查到真正写向量之间存在窗口，期间 demo reset 可能已提交归档事务
        # （租户 closed / 文档 failed 或已删除）。先 commit 刷新事务快照再查。
        db.commit()
        if not _doc_and_tenant_processable(db, doc_id, tenant_id):
            logger.info(
                f"Document {doc_id} or tenant {tenant_id} no longer processable "
                f"(reset/archived during processing), skip vector write"
            )
            return
        _dual_write_with_retry(doc.tenant_id, doc.id, doc.filename, chunks, vectors)

        # 双写期间可能被 reset/归档（向量已落入 Milvus/ES）→ 重查；
        # 异常时补偿清理该文档已写入的切片，不留旧租户向量。
        db.commit()
        if not _doc_and_tenant_processable(db, doc_id, tenant_id):
            logger.warning(
                f"Document {doc_id} tenant {tenant_id} archived during vector write, "
                f"compensating cleanup"
            )
            _compensate_vector_cleanup(doc_id)
            doc_after = knowledge_crud.get_by_id(db, doc_id, tenant_id)
            if doc_after is not None:
                knowledge_crud.update_status(
                    db, doc_after, "failed",
                    error_message="写入期间租户被 reset/归档，已补偿清理向量",
                )
                db.commit()
            return

        # 4. 更新文档状态
        knowledge_crud.update_status(db, doc, "ready")
        knowledge_crud.set_chunk_count(db, doc, len(chunks))
        db.commit()

        logger.info(f"Document {doc_id} processed: {len(chunks)} chunks")

    except Exception as e:
        logger.exception(f"Document processing failed: doc_id={doc_id}, error={e}")
        try:
            knowledge_crud.update_status(
                db, doc, "failed", error_message=str(e)[:1000]
            )
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def _doc_and_tenant_processable(db, doc_id: int, tenant_id: int) -> bool:
    """ 并发窗口防护：重查文档与租户是否仍可处理。

    demo reset 归档事务提交后：文档被置 failed 或删除、租户 status=closed。
    租户行不存在时不阻断（兼容未建租户行的存量环境；reset 从不删除租户行）。

    Args:
        db: 数据库会话（调用方需先 commit 刷新快照）
        doc_id: 文档 ID
        tenant_id: 租户 ID

    Returns:
        bool: True 表示可以继续写入向量
    """
    from app.models.tenant import Tenant

    doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)
    if doc is None or doc.status not in ("pending", "processing"):
        return False
    tenant = db.query(Tenant).filter_by(id=tenant_id).first()
    if tenant is not None and tenant.status != "active":
        return False
    return True


def _compensate_vector_cleanup(doc_id: int) -> None:
    """补偿清理：删除该文档已写入的 Milvus/ES 切片（尽力而为，失败仅记日志）。"""
    try:
        collection = get_milvus_collection()
        collection.delete(expr=f"doc_id == {doc_id}")
        # 基准/删除流程会立刻复查；显式 flush 避免已删除切片继续污染后续检索评测。
        collection.flush()
    except Exception as e:
        logger.warning(f"Compensate milvus cleanup failed for doc {doc_id}: {e}")
    try:
        get_es_client().delete_by_query(
            index="knowledge_chunks",
            body={"query": {"term": {"doc_id": doc_id}}},
            refresh=True,
        )
    except Exception as e:
        logger.warning(f"Compensate es cleanup failed for doc {doc_id}: {e}")


def _index_to_milvus(
    tenant_id: int, doc_id: int, doc_filename: str, chunks: list, vectors: list[list[float]]
) -> int:
    """将切片和向量写入 Milvus。返回成功写入的条数。

    写入前先删除该文档的旧切片（幂等），支持 rebuild 时清理旧版本。
    Milvus insert 接受列式数据（每字段一个 list）。

    Args:
        tenant_id: 租户 ID
        doc_id: 文档 ID
        doc_filename: 文档文件名
        chunks: 切片列表（Chunk 对象，含 index/text 属性）
        vectors: 向量列表，与 chunks 一一对应

    Returns:
        成功写入的条数
    """
    if not chunks:
        return 0

    collection = get_milvus_collection()
    # 版本管理：插入前清理该文档的旧切片（rebuild 时避免重复）
    try:
        collection.delete(expr=f"doc_id == {doc_id}")
    except Exception as e:
        logger.warning(f"Milvus cleanup old chunks for doc {doc_id} failed: {e}")
    # Milvus 列式插入：每个字段对应一个 list，顺序与 chunks 对齐
    data = [
        [tenant_id] * len(chunks),
        [doc_id] * len(chunks),
        [c.index for c in chunks],
        [c.text for c in chunks],
        [doc_filename] * len(chunks),
        vectors,
    ]
    result = collection.insert(data)
    return result.insert_count


def _index_to_es(
    tenant_id: int,
    doc_id: int,
    doc_filename: str,
    chunks: list,
    is_latest: bool = True,
) -> int:
    """通过 Bulk API 将切片批量写入 Elasticsearch，并返回成功条数。

    写入前先删除该文档的旧切片（幂等），支持 rebuild 时清理旧版本。
    每 500 条切片一次批量写入，避免大文档产生数千次 HTTP 往返。

    Args:
        tenant_id: 租户 ID
        doc_id: 文档 ID
        doc_filename: 文档文件名
        chunks: 切片列表（Chunk 对象，含 index/text 属性）
        is_latest: 是否为最新版本切片（旧版本不进检索库）

    Returns:
        成功写入的条数
    """
    if not chunks:
        return 0

    es = get_es_client()
    # 版本管理：插入新切片前清理该文档的旧版本切片（旧版本不进检索库）
    try:
        es.delete_by_query(
            index="knowledge_chunks",
            body={"query": {"term": {"doc_id": doc_id}}},
            refresh=True,
        )
    except Exception as e:
        logger.warning(f"ES cleanup old chunks for doc {doc_id} failed: {e}")
    operations: list[dict] = []
    for c in chunks:
        doc_body = {
            "tenant_id": tenant_id,
            "doc_id": doc_id,
            "chunk_index": c.index,
            "chunk_text": c.text,
            "doc_filename": doc_filename,
            "is_latest": is_latest,
        }
        # 固定文档 ID 让网络重试保持幂等，不会在 ES 中产生重复切片。
        operations.append({
            "index": {
                "_index": "knowledge_chunks",
                "_id": f"{tenant_id}:{doc_id}:{c.index}",
            }
        })
        operations.append(doc_body)

    count = 0
    operation_step = ES_BULK_CHUNK_SIZE * 2
    for start in range(0, len(operations), operation_step):
        response = es.options(request_timeout=60).bulk(
            operations=operations[start : start + operation_step]
        )
        body = response.body if hasattr(response, "body") else response
        if body.get("errors"):
            failures = [
                detail
                for item in body.get("items", [])
                for detail in item.values()
                if int(detail.get("status", 500)) >= 300
            ]
            raise RuntimeError(f"ES bulk 写入失败: {failures[:3]}")
        count += sum(
            1
            for item in body.get("items", [])
            for detail in item.values()
            if int(detail.get("status", 500)) < 300
        )
    # 刷新索引使新数据立即可搜索（默认 1s 刷新间隔，显式 refresh 保证实时）
    es.indices.refresh(index="knowledge_chunks")
    return count


def _dual_write_with_retry(
    tenant_id: int,
    doc_id: int,
    doc_filename: str,
    chunks: list,
    vectors: list[list[float]] | None,
) -> None:
    """写 Milvus + ES，任一启用侧失败则重试（最多 3 次），全部失败抛异常。

    重试策略:
      每轮尝试未成功的一侧（已成功的不重复写）。
      单侧写入是幂等的（先删旧再写新），故重试安全。
      3 次都失败则抛 RuntimeError，上层标记文档 failed。

    一致性说明:
      当前是"尽力一致"--Milvus 成功但 ES 失败时，Milvus 已有数据但 ES 没有。
      语义检索可命中但关键词检索缺失。生产环境应用 Outbox 模式或分布式事务。

    Args:
        tenant_id: 租户 ID
        doc_id: 文档 ID
        doc_filename: 文档文件名
        chunks: 切片列表
        vectors: 向量列表；None 表示 embedding 已关闭，只写 ES

    Raises:
        RuntimeError: 双写全部失败
    """
    milvus_ok = vectors is None
    es_ok = False

    for attempt in range(MAX_DUAL_WRITE_RETRIES):
        jobs: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            if not milvus_ok:
                assert vectors is not None
                jobs["Milvus"] = executor.submit(
                    _index_to_milvus,
                    tenant_id,
                    doc_id,
                    doc_filename,
                    chunks,
                    vectors,
                )
            if not es_ok:
                jobs["ES"] = executor.submit(
                    _index_to_es,
                    tenant_id,
                    doc_id,
                    doc_filename,
                    chunks,
                )

            for side, future in jobs.items():
                try:
                    future.result()  # type: ignore[union-attr]
                    if side == "Milvus":
                        milvus_ok = True
                    else:
                        es_ok = True
                except Exception as e:
                    logger.warning(f"{side} write attempt {attempt + 1} failed: {e}")

        if milvus_ok and es_ok:
            break

    if not milvus_ok or not es_ok:
        failed = []
        if not milvus_ok:
            failed.append("Milvus")
        if not es_ok:
            failed.append("ES")
        raise RuntimeError(f"双写失败（{', '.join(failed)}），已重试 {MAX_DUAL_WRITE_RETRIES} 次")


# 规格 010 US3: 知识库质量检测
def check_quality(chunks: list[str]) -> tuple[float, float]:
    """计算切片乱码率与空内容切片占比（PRD 3.2 解析质量检测）。

    乱码检测原理: 统计控制字符（ASCII < 32 且非 \\n\\r\\t）占全文的比例。
    返回 (乱码率, 空切片占比)；乱码率超 0.3 或空切片占比超 0.2 应标记
    pending_review 不进检索库。


    Args:
        chunks: 切片文本列表

    Returns:
        (乱码率 0.0-1.0, 空内容切片占比 0.0-1.0)
    """
    if not chunks:
        return 0.0, 0.0
    text = "".join(chunks)
    total = len(text)
    if total == 0:
        return 1.0, 1.0
    # 非可读字符：控制字符（除 \n\r\t 外）
    garbage = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    empty_ratio = sum(1 for c in chunks if not c.strip()) / len(chunks)
    return garbage / total, empty_ratio
