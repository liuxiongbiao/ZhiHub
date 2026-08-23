"""生成 9.5MB Markdown 并实测解析、向量化、Milvus/ES 双写全链路。

建议在后端容器内执行：
    python -m scripts.benchmark_document_pipeline --tenant-id 1 --size-mib 9.5

脚本使用唯一文档名，结束后清理数据库记录、上传文件与两个检索库中的基准切片。
"""

import argparse
import os
import time
from datetime import datetime, timezone

from app.config import settings
from app.core.database import SessionLocal
from app.crud import knowledge as knowledge_crud
from app.models.knowledge_document import KnowledgeDocument
from app.tasks.knowledge_tasks import _compensate_vector_cleanup, process_document


MARKDOWN_UNIT = """# 登录与套餐帮助

当登录页面一直转圈时，请先检查网络连接，清理浏览器缓存，然后重新登录。
如仍无法恢复，请记录发生时间和浏览器版本并联系在线客服。

## 套餐选择

基础版适合小团队，专业版提供更多会话额度，企业版包含高级权限与专属支持。
升级套餐后权益即时生效，降级在下个计费周期生效。

"""


def _build_markdown(target_bytes: int) -> bytes:
    """构造精确字节数且保持 UTF-8 合法的重复 Markdown。"""
    unit = MARKDOWN_UNIT.encode("utf-8")
    # 从完整 UTF-8 单元末尾裁剪，只追加 ASCII，避免截断中文多字节字符。
    full_units = target_bytes // len(unit)
    result = unit * full_units
    remaining = target_bytes - len(result)
    if remaining:
        result += (b"\n" + b"x" * max(0, remaining - 1))[:remaining]
    return result


def _create_document(tenant_id: int, filename: str, payload: bytes) -> tuple[int, str]:
    """创建基准文档元数据并把源文件写入正式共享上传目录。"""
    db = SessionLocal()
    try:
        doc = knowledge_crud.create(
            db,
            tenant_id=tenant_id,
            filename=filename,
            file_type="md",
            file_size=len(payload),
            version=1,
        )
        db.commit()
        db.refresh(doc)
        upload_path = os.path.join(settings.upload_storage_dir, f"{doc.id}_{filename}")
        os.makedirs(settings.upload_storage_dir, exist_ok=True)
        with open(upload_path, "wb") as file:
            file.write(payload)
        return doc.id, upload_path
    finally:
        db.close()


def _read_result(doc_id: int, tenant_id: int) -> tuple[str, int, str | None]:
    """读取任务完成后的状态、切片数和错误信息。"""
    db = SessionLocal()
    try:
        doc = knowledge_crud.get_by_id(db, doc_id, tenant_id)
        if doc is None:
            return "missing", 0, "文档记录不存在"
        return doc.status, doc.chunk_count, doc.error_message
    finally:
        db.close()


def _cleanup(doc_id: int, upload_path: str) -> None:
    """清理基准产生的向量、全文索引、源文件和文档记录。"""
    _compensate_vector_cleanup(doc_id)
    if os.path.isfile(upload_path):
        os.remove(upload_path)
    db = SessionLocal()
    try:
        db.query(KnowledgeDocument).filter_by(id=doc_id).delete()
        db.commit()
    finally:
        db.close()


def main() -> None:
    """执行一次完整文档管线并输出是否达到 180 秒目标。"""
    parser = argparse.ArgumentParser(description="ZhiHub 9.5MB 文档处理全链路基准")
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--size-mib", type=float, default=9.5)
    args = parser.parse_args()

    target_bytes = int(args.size_mib * 1024 * 1024)
    payload = _build_markdown(target_bytes)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"benchmark-{stamp}.md"
    doc_id, upload_path = _create_document(args.tenant_id, filename, payload)
    started = time.perf_counter()
    try:
        print(
            f"开始处理: doc={doc_id} size={len(payload) / 1024 / 1024:.2f}MiB "
            f"embedding={settings.embedding_enabled}",
            flush=True,
        )
        process_document.run(doc_id, args.tenant_id)
        elapsed = time.perf_counter() - started
        status, chunk_count, error = _read_result(doc_id, args.tenant_id)
        print(
            f"处理结果: status={status} chunks={chunk_count} elapsed={elapsed:.3f}s "
            f"target={'PASS' if elapsed < 180 and status == 'ready' else 'FAIL'}",
            flush=True,
        )
        if status != "ready":
            raise RuntimeError(error or f"文档处理状态异常: {status}")
        if elapsed >= 180:
            raise RuntimeError(f"文档处理耗时 {elapsed:.3f}s，未达到 180s 目标")
    finally:
        _cleanup(doc_id, upload_path)


if __name__ == "__main__":
    main()
