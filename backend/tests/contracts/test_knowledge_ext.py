"""
backend/tests/contracts/test_knowledge_ext.py

职责: 测试 #18 知识库数量上限（套餐配额）契约

测试内容:
  - 套餐配额: trial 1 / basic 3 / pro 无限
  - 旧版本归档不占配额（只统计 is_latest=True）
  - 上传超限 -> KnowledgeLimitExceeded 异常
"""

import io

import pytest
from fastapi import UploadFile

from app.crud import tenant as tenant_crud
from app.models.knowledge_document import KnowledgeDocument
from app.services import knowledge_service


def _make_doc(tenant_id: int, filename: str, is_latest: bool = True) -> KnowledgeDocument:
    """构造一个知识库文档记录（status=ready，is_latest 可控）。"""
    return KnowledgeDocument(
        tenant_id=tenant_id,
        filename=filename,
        file_type="md",
        file_size=100,
        status="ready",
        version=1,
        is_latest=is_latest,
    )


def _make_upload_file(filename: str = "test.md", content: bytes = b"# Test\n\nhello") -> UploadFile:
    """构造一个可被 knowledge_service.upload 接受的 UploadFile。"""
    return UploadFile(filename=filename, file=io.BytesIO(content))


class TestKnowledgeCountLimit:
    """套餐知识库数量上限契约（018）。"""

    def test_trial_limit_1(self, db):
        """trial 套餐上限 1: 0 个时允许，1 个后达上限。"""
        tenant = tenant_crud.create_tenant(db, name="t1", plan_type="trial")
        db.flush()

        r0 = knowledge_service.check_kb_count(db, tenant.id)
        assert r0["limit"] == 1
        assert r0["current"] == 0
        assert r0["allowed"] is True

        db.add(_make_doc(tenant.id, "a.md"))
        db.flush()
        r1 = knowledge_service.check_kb_count(db, tenant.id)
        assert r1["current"] == 1
        assert r1["allowed"] is False

    def test_basic_limit_3(self, db):
        """basic 套餐上限 3: 第 3 个仍允许，第 4 个拒绝。"""
        tenant = tenant_crud.create_tenant(db, name="t2", plan_type="basic")
        db.flush()

        for i in range(3):
            db.add(_make_doc(tenant.id, f"f{i}.md"))
            db.flush()
        r3 = knowledge_service.check_kb_count(db, tenant.id)
        assert r3["limit"] == 3
        assert r3["current"] == 3
        assert r3["allowed"] is False  # 已达上限

    def test_pro_unlimited(self, db):
        """pro 套餐无上限: limit=None，任意数量 allowed=True。"""
        tenant = tenant_crud.create_tenant(db, name="t3", plan_type="pro")
        db.flush()

        for i in range(10):
            db.add(_make_doc(tenant.id, f"f{i}.md"))
        db.flush()
        r = knowledge_service.check_kb_count(db, tenant.id)
        assert r["limit"] is None
        assert r["current"] == 10
        assert r["allowed"] is True

    def test_old_version_not_counted(self, db):
        """旧版本归档（is_latest=False）不占配额。"""
        tenant = tenant_crud.create_tenant(db, name="t4", plan_type="trial")
        db.flush()

        # 1 个最新 + 2 个归档旧版本 -> current 应为 1（trial 上限 1，达上限）
        db.add(_make_doc(tenant.id, "a.md", is_latest=True))
        db.add(_make_doc(tenant.id, "a.md", is_latest=False))
        db.add(_make_doc(tenant.id, "a.md", is_latest=False))
        db.flush()

        r = knowledge_service.check_kb_count(db, tenant.id)
        assert r["current"] == 1  # 只统计 is_latest=True
        assert r["allowed"] is False  # trial 上限 1，已达

    def test_upload_rejected_when_exceeded(self, db, monkeypatch):
        """trial 已有 1 个文档 -> upload 第 2 个抛 KnowledgeLimitExceeded。"""
        # mock rabbitmq 发布，避免连真实 MQ
        monkeypatch.setattr(
            "app.services.knowledge_service.publish_knowledge_task",
            lambda *a, **kw: None,
        )

        tenant = tenant_crud.create_tenant(db, name="t5", plan_type="trial")
        db.flush()
        db.add(_make_doc(tenant.id, "exist.md"))
        db.commit()

        with pytest.raises(knowledge_service.KnowledgeLimitExceeded):
            knowledge_service.upload(db, file=_make_upload_file("new.md"), tenant_id=tenant.id)

    def test_upload_allowed_under_limit(self, db, monkeypatch):
        """trial 0 个文档 -> upload 成功（未超限）。"""
        monkeypatch.setattr(
            "app.services.knowledge_service.publish_knowledge_task",
            lambda *a, **kw: None,
        )

        tenant = tenant_crud.create_tenant(db, name="t6", plan_type="trial")
        db.commit()

        result = knowledge_service.upload(
            db, file=_make_upload_file("first.md"), tenant_id=tenant.id
        )
        assert result["status"] == "pending"
        assert result["is_latest"] is True
