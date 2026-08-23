"""在隔离临时租户上实测 PRD §8.5 跨存储物理删除；不触碰现有租户。"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.database import SessionLocal
from app.core.embedding_client import (
    get_es_client,
    get_memory_collection,
    get_milvus_collection,
    init_es_index,
    init_memory_collection,
    init_milvus_collection,
)
from app.core.redis import get_redis
from app.models.knowledge_document import KnowledgeDocument
from app.models.message import Message
from app.models.order import Order
from app.models.refund_approval import RefundApproval
from app.models.refund_approval_event import RefundApprovalEvent
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.tenant_member import TenantMember
from app.models.user import User
from app.tasks.cleanup_tasks import _purge_expired_tenant, _utcnow


def main() -> int:
    marker = f"cancel-verify-{int(time.time() * 1000)}"
    suffix = str(int(time.time()))[-8:]
    db = SessionLocal()
    mongo = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    tenant_id = 0
    user_id = 0
    doc_id = 0
    session_id = 0
    upload_path: Path | None = None
    try:
        now = _utcnow()
        tenant = Tenant(
            name=marker,
            plan_type="basic",
            seat_limit=1,
            status="pending_deletion",
            cancellation_requested_at=now - timedelta(days=30, seconds=1),
            # MySQL DATETIME(0) may round fractional seconds upward; keep the fixture
            # one second overdue so the verifier cannot race the storage precision boundary.
            scheduled_deletion_at=now - timedelta(seconds=1),
            cancellation_previous_status="active",
        )
        user = User(phone=f"188{suffix}", password_hash="verification-only", role_default="admin")
        db.add_all([tenant, user])
        db.flush()
        tenant_id, user_id = tenant.id, user.id
        db.add(TenantMember(tenant_id=tenant_id, user_id=user_id, role="admin"))

        doc = KnowledgeDocument(
            tenant_id=tenant_id,
            filename=f"{marker}.md",
            file_type="md",
            file_size=12,
            status="ready",
            version=1,
            is_latest=True,
            chunk_count=1,
        )
        session = Session(tenant_id=tenant_id, status="resolved", effective_reply=True, billed=True)
        order = Order(
            tenant_id=tenant_id,
            order_no=f"CV-{int(time.time())}",
            amount=Decimal("1.00"),
            paid_amount=Decimal("1.00"),
            status="refunding",
        )
        db.add_all([doc, session, order])
        db.flush()
        doc_id, session_id = doc.id, session.id
        db.add(Message(session_id=session_id, role="assistant", content=marker))
        approval = RefundApproval(
            order_id=order.id,
            amount=Decimal("1.00"),
            status="pending",
            client_request_id=marker,
            approver_id=user_id,
        )
        db.add(approval)
        db.flush()
        db.add(
            RefundApprovalEvent(
                approval_id=approval.id,
                event_type="requested",
                actor_id=user_id,
                actor_role="admin",
            )
        )
        db.commit()

        upload_root = Path(settings.upload_storage_dir)
        upload_root.mkdir(parents=True, exist_ok=True)
        upload_path = upload_root / f"{doc_id}_{os.path.basename(doc.filename)}"
        upload_path.write_text("verification", encoding="utf-8")

        mongo.admin.command("ping")
        mongo.zhihub.agent_traces.insert_one({"tenant_id": tenant_id, "marker": marker})
        mongo.zhihub.deep_agent_tasks.insert_one({"tenant_id": tenant_id, "marker": marker})
        mongo.zhihub.analytics_events.insert_one({"tenant_id": tenant_id, "marker": marker})

        init_milvus_collection()
        init_memory_collection()
        vector = [0.001] * 1024
        knowledge = get_milvus_collection()
        knowledge.insert([[tenant_id], [doc_id], [0], [marker], [doc.filename], [vector]])
        knowledge.flush()
        memory = get_memory_collection()
        memory.insert([[tenant_id], [marker], ["verification"], [marker], [vector], [time.time()]])
        memory.flush()

        init_es_index()
        es = get_es_client()
        es.index(
            index="knowledge_chunks",
            id=marker,
            document={
                "tenant_id": tenant_id,
                "doc_id": doc_id,
                "chunk_index": 0,
                "chunk_text": marker,
                "doc_filename": doc.filename,
                "is_latest": True,
            },
            refresh=True,
        )
        redis_client = get_redis()
        redis_client.set(f"billing:quota:{tenant_id}:2026-08", "1")
        redis_client.set(f"session:context:{session_id}", marker)

        deleted = _purge_expired_tenant(db, tenant_id=tenant_id, now=now)

        knowledge.load()
        memory.load()
        checks = {
            "purge_returned": deleted,
            "mysql_tenant_removed": db.get(Tenant, tenant_id) is None,
            "mysql_orphan_user_removed": db.get(User, user_id) is None,
            "mongo_traces_removed": mongo.zhihub.agent_traces.count_documents({"tenant_id": tenant_id}) == 0,
            "mongo_tasks_removed": mongo.zhihub.deep_agent_tasks.count_documents({"tenant_id": tenant_id}) == 0,
            "mongo_analytics_removed": mongo.zhihub.analytics_events.count_documents({"tenant_id": tenant_id}) == 0,
            "milvus_knowledge_removed": len(knowledge.query(expr=f"tenant_id == {tenant_id}", output_fields=["id"])) == 0,
            "milvus_memory_removed": len(memory.query(expr=f"tenant_id == {tenant_id}", output_fields=["id"])) == 0,
            "elasticsearch_removed": es.count(
                index="knowledge_chunks", query={"term": {"tenant_id": tenant_id}}
            )["count"] == 0,
            "redis_removed": redis_client.get(f"billing:quota:{tenant_id}:2026-08") is None
            and redis_client.get(f"session:context:{session_id}") is None,
            "upload_removed": upload_path is not None and not upload_path.exists(),
        }
        report = {"marker": marker, "tenant_id": tenant_id, "checks": checks}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1
    finally:
        mongo.close()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
