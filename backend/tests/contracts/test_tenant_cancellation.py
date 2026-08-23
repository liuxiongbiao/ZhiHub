"""PRD §8.5：租户注销、30 天导出窗口与跨存储物理删除契约。"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile

from app.models.answer_feedback import AnswerFeedback
from app.models.audit_log import AuditLog
from app.models.billing_event import BillingEvent
from app.models.billing_invoice import BillingInvoice
from app.models.csat import CsatEvaluation
from app.models.knowledge_document import KnowledgeDocument
from app.models.message import Message
from app.models.notification import Notification
from app.models.order import Order
from app.models.quick_reply import QuickReply
from app.models.refund_approval import RefundApproval
from app.models.refund_approval_event import RefundApprovalEvent
from app.models.refund_outbox import RefundOutbox
from app.models.security_incident import SecurityIncident
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.tenant_member import TenantMember
from app.models.ticket import Ticket
from app.models.usage_record import UsageRecord
from app.models.user import User
from app.services import tenant_service
from app.tasks import cleanup_tasks


def _tenant_admin(db, *, status: str = "active", suffix: str = "1"):
    tenant = Tenant(name=f"注销测试{suffix}", plan_type="basic", seat_limit=2, status=status)
    user = User(phone=f"1380000{int(suffix):04d}", password_hash="hash", role_default="admin")
    db.add_all([tenant, user])
    db.flush()
    db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="admin"))
    db.commit()
    return tenant, user


def test_request_and_revoke_restores_previous_status(db):
    tenant, user = _tenant_admin(db, status="readonly")
    now = datetime(2026, 8, 24, 8, 0, 0)

    pending = tenant_service.request_tenant_cancellation(
        db, tenant.id, user_id=user.id, now=now
    )
    assert pending["status"] == "pending_deletion"
    assert pending["days_remaining"] == 30
    assert pending["scheduled_deletion_at"] == "2026-09-23T08:00:00Z"
    assert pending["can_revoke"] is True
    assert db.get(Tenant, tenant.id).cancellation_previous_status == "readonly"

    restored = tenant_service.revoke_tenant_cancellation(
        db, tenant.id, user_id=user.id, now=now + timedelta(days=2)
    )
    assert restored["status"] == "readonly"
    assert restored["deletion_pending"] is False
    row = db.get(Tenant, tenant.id)
    assert row.scheduled_deletion_at is None
    actions = [item.action for item in db.query(AuditLog).order_by(AuditLog.id).all()]
    assert actions == ["tenant_cancellation_requested", "tenant_cancellation_revoked"]


def test_cancellation_api_enforces_export_only_window(db, client, make_token):
    tenant, user = _tenant_admin(db, suffix="2")
    headers = {"Authorization": f"Bearer {make_token(user.id, tenant.id, 'admin')}"}

    response = client.post("/api/v1/tenant/cancellation", headers=headers)
    assert response.status_code == 200
    assert response.json()["deletion_pending"] is True

    assert client.get("/api/v1/tenant/home", headers=headers).status_code == 200
    assert client.get("/api/v1/tenant/cancellation", headers=headers).status_code == 200
    exported = client.get("/api/v1/tenant/export", headers=headers)
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    blocked = client.get("/api/v1/tenant/plans", headers=headers)
    assert blocked.status_code == 403
    assert "仅可导出数据或撤销注销" in blocked.json()["detail"]

    revoked = client.delete("/api/v1/tenant/cancellation", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "active"
    assert client.get("/api/v1/tenant/plans", headers=headers).status_code == 200


def test_cancellation_requires_admin(db, client, make_token):
    tenant, user = _tenant_admin(db, suffix="3")
    headers = {"Authorization": f"Bearer {make_token(user.id, tenant.id, 'agent')}"}
    assert client.post("/api/v1/tenant/cancellation", headers=headers).status_code == 403


def test_expired_cleanup_physically_deletes_relations_and_keeps_shared_user(db, monkeypatch):
    due = datetime(2026, 8, 24, 8, 0, 0)
    tenant, exclusive_user = _tenant_admin(db, suffix="4")
    shared_user = User(phone="13800000005", password_hash="hash", role_default="agent")
    other = Tenant(name="其他租户", plan_type="basic", seat_limit=2, status="active")
    db.add_all([shared_user, other])
    db.flush()
    db.add_all([
        TenantMember(tenant_id=tenant.id, user_id=shared_user.id, role="agent"),
        TenantMember(tenant_id=other.id, user_id=shared_user.id, role="admin"),
    ])
    tenant.status = "pending_deletion"
    tenant.cancellation_requested_at = due - timedelta(days=30)
    tenant.scheduled_deletion_at = due
    tenant.cancellation_previous_status = "active"
    db.flush()

    session = Session(
        tenant_id=tenant.id, status="resolved", assignee_id=exclusive_user.id,
        effective_reply=True, billed=True,
    )
    order = Order(
        tenant_id=tenant.id, order_no="ORD-CANCEL-1", amount=Decimal("100.00"),
        paid_amount=Decimal("100.00"), status="refunding",
    )
    db.add_all([session, order])
    db.flush()
    message = Message(session_id=session.id, role="assistant", content="answer")
    approval = RefundApproval(
        order_id=order.id, amount=Decimal("10.00"), status="pending",
        client_request_id="cancel-refund-1", approver_id=exclusive_user.id,
    )
    db.add_all([message, approval])
    db.flush()
    db.add_all([
        AnswerFeedback(message_id=message.id, score=-1, bad_case=True),
        CsatEvaluation(session_id=session.id, message_id=message.id, feedback="like", score=5),
        BillingEvent(
            session_id=session.id, tenant_id=tenant.id, event_key="session-cancel-1",
            model="test", tokens=10, usage_amount=Decimal("1.000000"),
        ),
        SecurityIncident(
            tenant_id=tenant.id, session_id=session.id, user_message_preview="masked",
            rule_hit="test", risk_level="low", action_taken="blocked",
        ),
        RefundApprovalEvent(
            approval_id=approval.id, event_type="requested", actor_id=exclusive_user.id,
            actor_role="admin",
        ),
        RefundOutbox(
            approval_id=approval.id, order_id=order.id, amount=Decimal("10.00"),
        ),
        KnowledgeDocument(
            tenant_id=tenant.id, filename="cancel.md", file_type="md", file_size=10,
            status="ready", version=1, is_latest=True, chunk_count=1,
        ),
        Ticket(
            tenant_id=tenant.id, ticket_no="TK-CANCEL-1", title="ticket",
            assignee_id=exclusive_user.id,
        ),
        QuickReply(tenant_id=tenant.id, content="reply"),
        Notification(tenant_id=tenant.id, user_id=exclusive_user.id, title="notice"),
        BillingInvoice(
            tenant_id=tenant.id, period="2026-08", base_fee=Decimal("1.00"),
            overage_fee=Decimal("0.00"), status="unpaid",
        ),
        UsageRecord(
            tenant_id=tenant.id, date=date(2026, 8, 24), conversation_count=1,
            is_overage=False,
        ),
        AuditLog(
            tenant_id=tenant.id, user_id=exclusive_user.id, action="seed",
            target_type="tenant", target_id=str(tenant.id),
        ),
    ])
    db.commit()
    tenant_id = tenant.id
    exclusive_user_id = exclusive_user.id
    shared_user_id = shared_user.id
    other_id = other.id

    external_calls = []
    monkeypatch.setattr(
        cleanup_tasks,
        "_purge_tenant_external_data",
        lambda **kwargs: external_calls.append(kwargs),
    )
    result = cleanup_tasks.cleanup_expired_tenant_cancellations(db, now=due)

    assert result == {"tenants_deleted": 1, "tenants_failed": 0}
    assert external_calls[0]["tenant_id"] == tenant_id
    assert db.get(Tenant, tenant_id) is None
    assert db.get(User, exclusive_user_id) is None
    assert db.get(User, shared_user_id) is not None
    assert db.get(Tenant, other_id) is not None
    assert db.query(Session).filter_by(tenant_id=tenant_id).count() == 0
    assert db.query(KnowledgeDocument).filter_by(tenant_id=tenant_id).count() == 0
    assert db.query(Order).filter_by(tenant_id=tenant_id).count() == 0


def test_external_purge_failure_retains_tenant_for_retry(db, monkeypatch):
    due = datetime(2026, 8, 24, 8, 0, 0)
    tenant, _ = _tenant_admin(db, suffix="6")
    tenant.status = "pending_deletion"
    tenant.cancellation_requested_at = due - timedelta(days=30)
    tenant.scheduled_deletion_at = due
    tenant.cancellation_previous_status = "active"
    db.commit()

    def fail(**_kwargs):
        raise RuntimeError("mongo unavailable")

    monkeypatch.setattr(cleanup_tasks, "_purge_tenant_external_data", fail)
    result = cleanup_tasks.cleanup_expired_tenant_cancellations(db, now=due)
    assert result == {"tenants_deleted": 0, "tenants_failed": 1}
    assert db.get(Tenant, tenant.id) is not None


def test_upload_file_cleanup_is_scoped_to_document_ids(monkeypatch):
    from app.config import settings

    data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_dir) as temporary:
        upload_dir = Path(temporary)
        monkeypatch.setattr(settings, "upload_storage_dir", str(upload_dir))
        target = upload_dir / "42_manual.md"
        untouched = upload_dir / "43_manual.md"
        target.write_text("delete", encoding="utf-8")
        untouched.write_text("keep", encoding="utf-8")

        cleanup_tasks._delete_tenant_upload_files([(42, "../manual.md")])
        assert not target.exists()
        assert untouched.exists()
