"""
backend/app/tasks/knowledge_tasks.py

职责: 消费 RabbitMQ 队列 -> 文档解析(规则切片，非 RagFlow) -> 调用云端 embedding API 向量化
      -> 并行写入 Milvus + ES -> 双写不一致重试 -> 更新文档状态
分工: 🟡 主流程弱模型能写，双写不一致重试的边界由强模型定
对应日程: Day 3 / 003-knowledge-base

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
  - 上游: app/core/rabbitmq.py consume_knowledge_tasks（消息驱动）
  - 下游: app.core.embedding_client（embedding/ES/Milvus）、app.crud.knowledge、
          app.utils.file_parser（解析切片）
"""

import logging
import os

from sqlalchemy.orm import Session

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

        # 更新状态为 processing（标记开始处理，前端可显示进度）
        knowledge_crud.update_status(db, doc, "processing")
        db.commit()

        # 确保 Milvus collection + ES index 存在（首次上传时初始化）
        try:
            init_milvus_collection()
            init_es_index()
        except Exception as e:
            logger.warning(f"Init vector store failed (non-blocking): {e}")

        # 从本地磁盘读取文件内容（upload 时写入的临时文件）
        import tempfile
        tmp_path = os.path.join(tempfile.gettempdir(), "zhihub_uploads", f"{doc_id}_{os.path.basename(doc.filename)}")
        if not os.path.exists(tmp_path):
            # 临时文件不存在（可能被清理或重启丢失），降级为 ready + 0 chunks 避免阻塞队列
            logger.warning(f"Temp file not found: {tmp_path}, falling back to mock mode")
            knowledge_crud.update_status(db, doc, "ready")
            knowledge_crud.set_chunk_count(db, doc, 0)
            db.commit()
            return

        with open(tmp_path, "rb") as f:
            content = f.read()

        # 1. 解析 + 切片
        chunks = parse_and_chunk(doc.filename, content)
        if not chunks:
            knowledge_crud.update_status(db, doc, "failed", error_message="文档解析后无有效文本内容")
            db.commit()
            return

        # 2. 批量 Embedding
        chunk_texts = [c.text for c in chunks]

        # 乱码率检测（010 US3）：超阈值标记 pending_review 不进检索库
        quality_rate = check_quality(chunk_texts)
        if quality_rate > 0.3:
            doc.quality_status = "pending_review"
            doc.status = "ready"
            doc.error_message = f"乱码率过高({quality_rate:.2f})，待人工复核"
            db.commit()
            logger.warning(f"Document {doc_id} pending_review: garbage rate {quality_rate:.2f}")
            return

        vectors = embed_batches(chunk_texts)
        if len(vectors) != len(chunks):
            knowledge_crud.update_status(db, doc, "failed", error_message="Embedding向量数与切片数不匹配")
            db.commit()
            return

        # 3. 双写 Milvus + ES（重试 3 次）
        _dual_write_with_retry(doc.tenant_id, doc.id, doc.filename, chunks, vectors)

        # 4. 更新文档状态
        knowledge_crud.update_status(db, doc, "ready")
        knowledge_crud.set_chunk_count(db, doc, len(chunks))
        db.commit()

        # 5. 清理临时文件
        try:
            os.remove(tmp_path)
        except OSError:
            pass

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
    """将切片写入 Elasticsearch。返回成功写入的条数。

    写入前先删除该文档的旧切片（幂等），支持 rebuild 时清理旧版本。
    ES 逐条 index（非 bulk），量大时应改用 bulk API 优化性能。

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
    count = 0
    for c in chunks:
        doc_body = {
            "tenant_id": tenant_id,
            "doc_id": doc_id,
            "chunk_index": c.index,
            "chunk_text": c.text,
            "doc_filename": doc_filename,
            "is_latest": is_latest,
        }
        es.index(index="knowledge_chunks", document=doc_body)
        count += 1
    # 刷新索引使新数据立即可搜索（默认 1s 刷新间隔，显式 refresh 保证实时）
    es.indices.refresh(index="knowledge_chunks")
    return count


def _dual_write_with_retry(
    tenant_id: int,
    doc_id: int,
    doc_filename: str,
    chunks: list,
    vectors: list[list[float]],
) -> None:
    """双写 Milvus + ES，任一侧失败则重试（最多 3 次），全部失败抛异常。

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
        vectors: 向量列表

    Raises:
        RuntimeError: 双写全部失败
    """
    milvus_ok = False
    es_ok = False

    for attempt in range(MAX_DUAL_WRITE_RETRIES):
        if not milvus_ok:
            try:
                _index_to_milvus(tenant_id, doc_id, doc_filename, chunks, vectors)
                milvus_ok = True
            except Exception as e:
                logger.warning(f"Milvus write attempt {attempt + 1} failed: {e}")

        if not es_ok:
            try:
                _index_to_es(tenant_id, doc_id, doc_filename, chunks)
                es_ok = True
            except Exception as e:
                logger.warning(f"ES write attempt {attempt + 1} failed: {e}")

        if milvus_ok and es_ok:
            break

    if not milvus_ok or not es_ok:
        failed = []
        if not milvus_ok:
            failed.append("Milvus")
        if not es_ok:
            failed.append("ES")
        raise RuntimeError(f"双写失败（{', '.join(failed)}），已重试 {MAX_DUAL_WRITE_RETRIES} 次")


# 010 US3: 知识库质量检测
def check_quality(chunks: list[str]) -> float:
    """计算切片乱码率（非可读字符占比，010 US3）。

    乱码检测原理: 统计控制字符（ASCII < 32 且非 \\n\\r\\t）占全文的比例。
    返回 0.0-1.0，超 0.3 应标记 pending_review 不进检索库。


    Args:
        chunks: 切片文本列表

    Returns:
        乱码率（0.0-1.0），0.0 表示无乱码
    """
    if not chunks:
        return 0.0
    text = "".join(chunks)
    total = len(text)
    if total == 0:
        return 1.0
    # 非可读字符：控制字符（除 \n\r\t 外）
    garbage = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
    return garbage / total
