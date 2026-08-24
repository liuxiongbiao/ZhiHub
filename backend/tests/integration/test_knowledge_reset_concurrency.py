"""集成测试： 知识任务与 demo reset 的并发窗口。

覆盖两个时序（必须越过 worker 的第一次状态检查后才触发归档，不允许只测
"任务启动前文档已 failed"）：
  A. worker 通过状态检查后、真正写入 Milvus/ES 前，reset 提交归档事务
     -> worker 不得写入向量
  B. worker 双写过程中 reset 提交归档事务（向量已写入）
     -> worker 必须补偿清理该文档切片，不留旧租户向量

归档事务对 DB 的可观察效果与 demo_seed.reset 完全一致：tenant.status=closed、
文档置 failed（reset 语义），因此这里以同样的 DB 变更模拟 reset 已提交。
"""

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models import (  # noqa: F401 注册全部模型
    knowledge_document as _kd,
    tenant as _tt,
)


@pytest.fixture()
def kb_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class _RecordingCollection:
    """记录 insert/delete 的 Milvus 桩。"""

    def __init__(self, log):
        self._log = log

    def insert(self, data):
        self._log.append(("milvus_insert", data[1][0] if data else None))

        class _R:
            insert_count = len(data[0]) if data else 0

        return _R()

    def delete(self, expr):
        self._log.append(("milvus_delete", expr))


class _RecordingIndices:
    def refresh(self, *a, **k):
        return {}


class _RecordingES:
    def __init__(self, log):
        self._log = log
        self.indices = _RecordingIndices()

    def index(self, *a, **k):
        self._log.append(("es_index", k.get("document", {}).get("doc_id")))

    def options(self, **_kwargs):
        return self

    def bulk(self, *, operations):
        items = []
        for offset in range(0, len(operations), 2):
            meta = operations[offset]["index"]
            document = operations[offset + 1]
            self._log.append(("es_index", document.get("doc_id")))
            items.append({"index": {"_id": meta["_id"], "status": 201}})
        return {"errors": False, "items": items}

    def delete_by_query(self, *a, **k):
        body = (a[1] if len(a) > 1 else k.get("body")) or {}
        self._log.append(("es_delete", body))


def _patch_infra(monkeypatch, log):
    import app.tasks.knowledge_tasks as kt

    monkeypatch.setattr(kt.settings, "embedding_enabled", True)  # 显式开启，保证注入点必经
    monkeypatch.setattr(kt, "init_milvus_collection", lambda: None)
    monkeypatch.setattr(kt, "init_es_index", lambda: None)
    monkeypatch.setattr(kt, "embed_batches", lambda texts: [[0.1] * 8 for _ in texts])
    monkeypatch.setattr(kt, "get_milvus_collection", lambda: _RecordingCollection(log))
    monkeypatch.setattr(kt, "get_es_client", lambda: _RecordingES(log))
    from app.core import ragflow_client

    class _MockRagFlow:
        is_mock = True

    monkeypatch.setattr(ragflow_client, "get_ragflow_client", lambda: _MockRagFlow())
    return kt


def _make_active_demo_tenant(db, tenant_id=1):
    from app.models.tenant import Tenant

    db.add(Tenant(id=tenant_id, name="并发测试演示租户", plan_type="enterprise",
                  status="active", is_demo=True))
    db.commit()


def _make_doc(db, tenant_id=1, filename="并发窗口.md"):
    from app.models.knowledge_document import KnowledgeDocument

    doc = KnowledgeDocument(
        tenant_id=tenant_id, filename=filename, file_type="md",
        file_size=100, status="pending", quality_status="ready",
    )
    db.add(doc)
    db.commit()
    return doc


def _write_temp_file(doc_id, filename, content):
    tmp_dir = os.path.join(tempfile.gettempdir(), "zhihub_uploads")
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, f"{doc_id}_{filename}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _commit_archive(kb_db, tenant_id, doc_id):
    """模拟 demo_seed.reset 已提交的归档事务（相同的 DB 变更）。

    reset 语义：tenant.status=closed、pending/processing 文档置 failed 后删除文档记录。
    """
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.tenant import Tenant

    s = kb_db()
    try:
        t = s.query(Tenant).filter_by(id=tenant_id).first()
        if t is not None:
            t.status = "closed"
        d = s.query(KnowledgeDocument).filter_by(id=doc_id).first()
        if d is not None:
            if d.status in ("pending", "processing"):
                d.status = "failed"
                d.error_message = "演示租户 reset 归档清理"
            s.delete(d)
        s.commit()
    finally:
        s.close()


class TestKnowledgeResetConcurrency:
    def test_reset_after_first_check_before_write_blocks_vector_write(self, kb_db, monkeypatch):
        """时序 A：状态检查已通过、即将写入时 reset 归档提交 -> 不得写入向量。"""
        from app.tasks.knowledge_tasks import process_document

        log = []
        kt = _patch_infra(monkeypatch, log)
        db = kb_db()
        monkeypatch.setattr(kt, "SessionLocal", lambda: db)
        _make_active_demo_tenant(db)
        doc = _make_doc(db)
        _write_temp_file(doc.id, doc.filename, "第一条知识\n第二条知识\n")
        doc_id = doc.id

        # 注入点：worker 已通过流程前段的所有检查、正要双写时，reset 提交归档事务。
        # 若写入前重查缺失，该 wrapper 会被调用并记录（测试失败）。
        def dual_write_should_not_run(tenant_id, d_id, name, chunks, vectors):
            log.append(("dual_write_called", d_id))

        monkeypatch.setattr(kt, "_dual_write_with_retry", dual_write_should_not_run)

        def embed_with_reset_injected(texts):
            # embedding 阶段（第一次状态检查之后）reset 提交归档
            _commit_archive(kb_db, tenant_id=1, doc_id=doc_id)
            return [[0.1] * 8 for _ in texts]

        monkeypatch.setattr(kt, "embed_batches", embed_with_reset_injected)

        process_document(doc_id, tenant_id=1)

        writes = [e for e in log if e[0] in ("milvus_insert", "es_index", "dual_write_called")]
        assert writes == [], f"归档后仍尝试写入向量: {writes}"
        # 文档已删除（reset 语义），不得复活为 ready
        check = kb_db()
        try:
            from app.models.knowledge_document import KnowledgeDocument
            assert check.query(KnowledgeDocument).get(doc_id) is None
        finally:
            check.close()

    def test_reset_during_write_triggers_compensating_cleanup(self, kb_db, monkeypatch):
        """时序 B：双写完成后发现写入期间已归档 -> 补偿删除该文档切片。"""
        from app.tasks.knowledge_tasks import process_document

        log = []
        kt = _patch_infra(monkeypatch, log)
        db = kb_db()
        monkeypatch.setattr(kt, "SessionLocal", lambda: db)
        _make_active_demo_tenant(db)
        doc = _make_doc(db, filename="写入中归档.md")
        _write_temp_file(doc.id, doc.filename, "第一条知识\n第二条知识\n")
        doc_id = doc.id

        real_dual_write = kt._dual_write_with_retry

        def dual_write_with_reset_injected(tenant_id, d_id, name, chunks, vectors):
            real_dual_write(tenant_id, d_id, name, chunks, vectors)
            # 双写刚完成，reset 归档事务在此提交（向量已落入 Milvus/ES）
            _commit_archive(kb_db, tenant_id=tenant_id, doc_id=d_id)

        monkeypatch.setattr(kt, "_dual_write_with_retry", dual_write_with_reset_injected)

        process_document(doc_id, tenant_id=1)

        assert any(e[0] == "milvus_insert" and e[1] == doc_id for e in log), "前置：向量确实已写入"
        assert any(e[0] == "es_index" and e[1] == doc_id for e in log), "前置：ES 切片确实已写入"
        # 补偿清理：按 doc_id 删除 Milvus / ES 切片
        assert any(e[0] == "milvus_delete" and f"doc_id == {doc_id}" in e[1] for e in log), \
            f"缺少 Milvus 补偿清理: {log}"
        assert any(
            e[0] == "es_delete" and (e[1].get("query") or {}).get("term", {}).get("doc_id") == doc_id
            for e in log
        ), f"缺少 ES 补偿清理: {log}"
        # 文档未伪装 ready
        check = kb_db()
        try:
            from app.models.knowledge_document import KnowledgeDocument
            fresh = check.query(KnowledgeDocument).get(doc_id)
            assert fresh is None or fresh.status != "ready"
        finally:
            check.close()

    def test_active_tenant_normal_write_unaffected(self, kb_db, monkeypatch):
        """无归档并发时正常写入（回归保护：新增的重查不得破坏正常流程）。"""
        from app.models.knowledge_document import KnowledgeDocument
        from app.tasks.knowledge_tasks import process_document

        log = []
        kt = _patch_infra(monkeypatch, log)
        db = kb_db()
        monkeypatch.setattr(kt, "SessionLocal", lambda: db)
        _make_active_demo_tenant(db)
        doc = _make_doc(db, filename="正常流程.md")
        _write_temp_file(doc.id, doc.filename, "第一条知识\n第二条知识\n")
        doc_id = doc.id

        process_document(doc_id, tenant_id=1)

        check = kb_db()
        try:
            fresh = check.query(KnowledgeDocument).get(doc_id)
            assert fresh.status == "ready" and fresh.chunk_count > 0
        finally:
            check.close()
