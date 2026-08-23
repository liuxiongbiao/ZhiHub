"""
backend/tests/integration/test_knowledge_tasks.py

职责: 知识库异步任务测试（: 原 5 个无条件 skip 改为 mock 基础设施的真测试）

测试内容:
  - TestProcessDocument: pending->ready 状态流转 / embedding 失败标 failed / 临时文件缺失降级
  - TestDualWrite: 双写成功 / 单侧失败重试 / 全失败抛异常

设计决策:
  基础设施（Milvus/ES/embedding API）全部 monkeypatch 桩掉（patch 源模块 embedding_client
  的属性——process_document 内部是函数内 import），业务逻辑真实执行。
  每个测试自建 sqlite engine + 独立 session（process_document 会 close 自己的 session，
  不能复用 conftest 的 db fixture）。
"""
import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models import knowledge_document as _kd  # noqa: F401 注册模型


@pytest.fixture
def kb_db():
    """独立 sqlite session（process_document 会关闭 SessionLocal 的 session）。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class _FakeCollection:
    def delete(self, expr):
        pass

    def insert(self, data):
        class _R:
            insert_count = len(data[0]) if data else 0
        return _R()

    def search(self, *a, **k):
        return []


class _FakeIndices:
    def refresh(self, *a, **k):
        return {}


class _FakeES:
    """模拟 Elasticsearch Bulk API 与索引刷新行为。"""

    def __init__(self):
        self.indices = _FakeIndices()  # 属性（_index_to_es 调 es.indices.refresh(...)）

    def options(self, **kwargs):
        """忽略传输层超时选项并返回自身。"""
        return self

    def bulk(self, *, operations):
        """按 action/source 成对返回成功结果。"""
        items = [{"index": {"status": 201}} for _ in range(0, len(operations), 2)]
        return {"errors": False, "items": items}

    def delete_by_query(self, *a, **k):
        return {}

    def index(self, *a, **k):
        return {}

    def search(self, *a, **k):
        return {"hits": {"total": 0, "hits": []}}


def _patch_infra(monkeypatch):
    """桩掉 knowledge_tasks 模块的基础设施绑定（模块级 import 的名字）。

    knowledge_tasks 在模块顶部 `from app.core.embedding_client import ...`，
    get_milvus_collection 等名字绑定在 kt 命名空间——必须 patch kt 模块属性
    （patch 源模块 ec 对函数内 import 有效，对模块级绑定无效）。
    """
    import app.tasks.knowledge_tasks as kt

    monkeypatch.setattr(kt, "init_milvus_collection", lambda: None)
    monkeypatch.setattr(kt, "init_es_index", lambda: None)
    monkeypatch.setattr(kt, "embed_batches", lambda texts: [[0.1] * 8 for _ in texts])
    monkeypatch.setattr(kt, "get_milvus_collection", lambda: _FakeCollection())
    monkeypatch.setattr(kt, "get_es_client", lambda: _FakeES())
    from app.core import ragflow_client

    class _MockRagFlow:
        is_mock = True

    monkeypatch.setattr(ragflow_client, "get_ragflow_client", lambda: _MockRagFlow())
    return kt


def _make_doc(db, filename="kb.md"):
    from app.models.knowledge_document import KnowledgeDocument

    doc = KnowledgeDocument(
        tenant_id=1, filename=filename, file_type="md",
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


class TestProcessDocument:
    """process_document 异步任务状态流转。"""

    def test_pending_to_ready_transition(self, kb_db, monkeypatch):
        """文档处理成功: pending -> ready + chunk_count > 0。"""
        from app.models.knowledge_document import KnowledgeDocument
        from app.tasks.knowledge_tasks import process_document

        kt = _patch_infra(monkeypatch)
        db = kb_db()
        monkeypatch.setattr(kt, "SessionLocal", lambda: db)
        doc = _make_doc(db)
        _write_temp_file(doc.id, doc.filename, "第一条知识\n第二条知识\n")
        doc_id = doc.id  # 保存 id（doc 实例随 session 关闭失效，事后用新 session 查）

        process_document(doc_id, tenant_id=1)

        check_db = kb_db()
        fresh = check_db.get(KnowledgeDocument, doc_id)
        assert fresh.status == "ready", f"应 ready, got {fresh.status}"
        assert fresh.chunk_count > 0

    def test_embedding_disabled_indexes_keywords_without_external_call(self, kb_db, monkeypatch):
        """关闭 embedding 时不发外部请求，仍写 ES 并把文档置为 ready。"""
        from app.models.knowledge_document import KnowledgeDocument
        from app.tasks.knowledge_tasks import process_document

        kt = _patch_infra(monkeypatch)
        monkeypatch.setattr(kt.settings, "embedding_enabled", False)
        monkeypatch.setattr(
            kt,
            "embed_batches",
            lambda texts: (_ for _ in ()).throw(AssertionError("embedding must stay disabled")),
        )
        writes = {"milvus": 0, "es": 0}
        monkeypatch.setattr(
            kt,
            "_index_to_milvus",
            lambda *a, **k: writes.__setitem__("milvus", writes["milvus"] + 1),
        )
        monkeypatch.setattr(
            kt,
            "_index_to_es",
            lambda *a, **k: writes.__setitem__("es", writes["es"] + 1),
        )
        db = kb_db()
        monkeypatch.setattr(kt, "SessionLocal", lambda: db)
        doc = _make_doc(db, filename="keyword-only.md")
        _write_temp_file(doc.id, doc.filename, "关闭向量服务时仍应支持关键词检索\n第二条知识\n")
        doc_id = doc.id

        process_document(doc_id, tenant_id=1)

        check_db = kb_db()
        fresh = check_db.get(KnowledgeDocument, doc_id)
        assert fresh.status == "ready", f"{fresh.status}: {fresh.error_message}"
        assert fresh.chunk_count > 0
        assert writes == {"milvus": 0, "es": 1}

    def test_embedding_api_failure_marks_failed(self, kb_db, monkeypatch):
        """Embedding API 失败 -> 文档标记 failed。"""
        from app.models.knowledge_document import KnowledgeDocument
        from app.tasks.knowledge_tasks import process_document

        kt = _patch_infra(monkeypatch)
        monkeypatch.setattr(kt.settings, "embedding_enabled", True)
        monkeypatch.setattr(kt, "embed_batches", lambda texts: (_ for _ in ()).throw(RuntimeError("embed api down")))
        db = kb_db()
        monkeypatch.setattr(kt, "SessionLocal", lambda: db)
        doc = _make_doc(db)
        _write_temp_file(doc.id, doc.filename, "内容")
        doc_id = doc.id

        process_document(doc_id, tenant_id=1)

        check_db = kb_db()
        fresh = check_db.get(KnowledgeDocument, doc_id)
        assert fresh.status == "failed"

    def test_temp_file_missing_marks_failed(self, kb_db, monkeypatch):
        """文件缺失 -> 标记 failed，严禁 ready + 0 chunks。"""
        from app.models.knowledge_document import KnowledgeDocument
        from app.tasks.knowledge_tasks import process_document

        kt = _patch_infra(monkeypatch)
        db = kb_db()
        monkeypatch.setattr(kt, "SessionLocal", lambda: db)
        doc = _make_doc(db, filename="missing-file.md")  # 独特文件名：不写临时文件
        doc_id = doc.id

        process_document(doc_id, tenant_id=1)

        check_db = kb_db()
        fresh = check_db.get(KnowledgeDocument, doc_id)
        assert fresh.status == "failed"
        assert fresh.chunk_count == 0
        assert fresh.error_message


class TestDualWrite:
    """_dual_write_with_retry 双写与重试。"""

    def test_both_writes_succeed(self, monkeypatch):
        """双写成功: 无异常，两侧各写一次。"""
        from app.tasks.knowledge_tasks import _dual_write_with_retry

        kt = _patch_infra(monkeypatch)
        calls = {"m": 0, "e": 0}
        monkeypatch.setattr(kt, "_index_to_milvus", lambda *a, **k: calls.__setitem__("m", calls["m"] + 1))
        monkeypatch.setattr(kt, "_index_to_es", lambda *a, **k: calls.__setitem__("e", calls["e"] + 1))

        _dual_write_with_retry(1, 1, "a.md", ["c1"], [[0.1]])
        assert calls == {"m": 1, "e": 1}

    def test_milvus_recovers_on_retry(self, monkeypatch):
        """Milvus 前 2 次失败第 3 次成功: 重试后成功（成功侧不重复写）。"""
        from app.tasks.knowledge_tasks import _dual_write_with_retry

        kt = _patch_infra(monkeypatch)
        m_calls = {"n": 0}
        e_calls = {"n": 0}
        monkeypatch.setattr(
            kt, "_index_to_milvus",
            lambda *a, **k: (m_calls.__setitem__("n", m_calls["n"] + 1)
                             or ((_ for _ in ()).throw(RuntimeError("milvus down")) if m_calls["n"] < 3 else None)),
        )
        monkeypatch.setattr(kt, "_index_to_es", lambda *a, **k: e_calls.__setitem__("n", e_calls["n"] + 1))

        _dual_write_with_retry(1, 1, "a.md", ["c1"], [[0.1]])
        assert m_calls["n"] == 3, f"Milvus 应重试 3 次后成功, got {m_calls}"
        assert e_calls["n"] == 1, "ES 成功侧不重复写"

    def test_milvus_fails_all_retries_raises(self, monkeypatch):
        """Milvus 恒失败: 3 次重试后抛 RuntimeError。"""
        from app.tasks.knowledge_tasks import _dual_write_with_retry

        kt = _patch_infra(monkeypatch)
        m_calls = {"n": 0}
        monkeypatch.setattr(kt, "_index_to_milvus", lambda *a, **k: m_calls.__setitem__("n", m_calls["n"] + 1) or (_ for _ in ()).throw(RuntimeError("milvus down")))
        monkeypatch.setattr(kt, "_index_to_es", lambda *a, **k: 1)

        with pytest.raises(RuntimeError):
            _dual_write_with_retry(1, 1, "a.md", ["c1"], [[0.1]])
        assert m_calls["n"] == 3

    def test_both_fail_raises(self, monkeypatch):
        """两侧都失败 -> 抛 RuntimeError。"""
        from app.tasks.knowledge_tasks import _dual_write_with_retry

        kt = _patch_infra(monkeypatch)
        monkeypatch.setattr(kt, "_index_to_milvus", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        monkeypatch.setattr(kt, "_index_to_es", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

        with pytest.raises(RuntimeError):
            _dual_write_with_retry(1, 1, "a.md", ["c1"], [[0.1]])
