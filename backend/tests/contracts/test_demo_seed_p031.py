"""契约测试： 电商演示租户收尾（退款一致性 / 不可变事件下 reset 归档 /
全数据幂等 / 密码与身份隔离 / 知识失败与 reset 清理）。"""

import pytest

from app.crud import tenant as tenant_crud
from app.models.order import Order
from app.models.refund_approval import RefundApproval
from app.models.ticket import Ticket

DEMO_ADMIN_PASSWORD = "DemoAdmin@12345"
DEMO_AGENT_PASSWORD = "DemoAgent@12345"


@pytest.fixture()
def demo_env(monkeypatch, db):
    """注入固定演示密码 + fake 知识上传（直接 ready）。"""
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", DEMO_ADMIN_PASSWORD)
    monkeypatch.setenv("DEMO_AGENT_PASSWORD", DEMO_AGENT_PASSWORD)

    from app.crud import knowledge as knowledge_crud

    def fake_upload(db_, *, file, tenant_id, expires_days=None):
        doc = knowledge_crud.create(
            db_, tenant_id=tenant_id, filename=file.filename,
            file_type="md", file_size=1, version=1,
        )
        db_.flush()
        knowledge_crud.update_status(db_, doc, "ready")
        knowledge_crud.set_chunk_count(db_, doc, 3)
        db_.commit()
        return {"doc_id": doc.id, "filename": file.filename, "status": "ready"}

    from app.demo import demo_seed
    monkeypatch.setattr(demo_seed, "_upload_knowledge", fake_upload)
    return {"db": db}


def _count(db, model, tenant_id=None):
    q = db.query(model)
    if tenant_id is not None:
        q = q.filter(model.tenant_id == tenant_id)
    return q.count()


# ═══════════════════════════════════════════════════════════
# 一、退款演示数据一致性
# ═══════════════════════════════════════════════════════════
class TestRefundConsistency:
    def test_preset_refunds_follow_state_machine(self, demo_env):
        from app.demo import demo_seed
        from app.models.refund_approval_event import RefundApprovalEvent
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]

        # 0006：已进入退款审批（订单 refund_requested + pending approval + requested 事件 + pre_status）
        o6 = db.query(Order).filter_by(tenant_id=tid, order_no="ORD-DEMO-0006").first()
        assert o6.status == "refund_requested"
        a6 = db.query(RefundApproval).filter_by(order_id=o6.id).first()
        assert a6 is not None and a6.status == "pending"
        assert a6.pre_status == "paid"
        events6 = db.query(RefundApprovalEvent).filter_by(approval_id=a6.id).all()
        assert any(e.event_type == "requested" for e in events6)

        # 0002：小额终态退款（rejected 事件链完整），订单回退 paid（仍可发货）
        o2 = db.query(Order).filter_by(tenant_id=tid, order_no="ORD-DEMO-0002").first()
        assert o2.status == "paid"
        a2 = db.query(RefundApproval).filter_by(order_id=o2.id).first()
        assert a2 is not None and a2.status == "rejected"
        types2 = [e.event_type for e in db.query(RefundApprovalEvent).filter_by(approval_id=a2.id).all()]
        assert "requested" in types2 and "rejected" in types2

        # 0005：E2E eligible（无活动退款申请）
        o5 = db.query(Order).filter_by(tenant_id=tid, order_no="ORD-DEMO-0005").first()
        assert o5.status == "paid"
        active5 = db.query(RefundApproval).filter_by(order_id=o5.id, status="pending").count()
        assert active5 == 0

        # 每订单最多一个活动退款申请
        for ono in ("ORD-DEMO-0001", "ORD-DEMO-0002", "ORD-DEMO-0003", "ORD-DEMO-0004", "ORD-DEMO-0005", "ORD-DEMO-0006"):
            o = db.query(Order).filter_by(tenant_id=tid, order_no=ono).first()
            assert db.query(RefundApproval).filter_by(order_id=o.id, status="pending").count() <= 1

    def test_all_preset_approvals_have_requested_event(self, demo_env):
        from app.demo import demo_seed
        from app.models.refund_approval_event import RefundApprovalEvent
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        order_ids = [o.id for o in db.query(Order).filter_by(tenant_id=tid).all()]
        approvals = db.query(RefundApproval).filter(RefundApproval.order_id.in_(order_ids)).all()
        assert len(approvals) == 2
        for a in approvals:
            assert db.query(RefundApprovalEvent).filter_by(approval_id=a.id).count() >= 1


# ═══════════════════════════════════════════════════════════
# 二、不可变事件下的 reset（归档旧实例）
# ═══════════════════════════════════════════════════════════
class TestResetArchives:
    def _run_full_approval_flow(self, db, tid, order_no, amount, decision):
        """走真实状态机：申请 -> 审批，产生不可变事件。"""
        from app.services.order_service import approve_refund, request_refund
        order = db.query(Order).filter_by(tenant_id=tid, order_no=order_no).first()
        result = request_refund(
            db, tenant_id=tid, order_no=order_no, amount=amount,
            reason="p031 测试", client_request_id=f"p031-{order_no}",
            phone="13911112222",
        )
        db.commit()
        approve_refund(db, approval_id=result["id"], decision=decision,
                       approver_id=1, tenant_id=tid, role="admin")
        db.commit()
        return result["id"]

    def test_reset_archives_and_new_instance_restores(self, demo_env):
        from app.demo import demo_seed
        from app.models.refund_approval_event import RefundApprovalEvent
        from app.models.tenant import Tenant
        db = demo_env["db"]
        r1 = demo_seed.seed(db=db)
        old_tid = r1["tenant_id"]

        # 在 0005 上走真实审批流程（产生 approved 事件）
        approval_id = self._run_full_approval_flow(db, old_tid, "ORD-DEMO-0005", 50.0, "approved")
        events_before = db.query(RefundApprovalEvent).filter_by(approval_id=approval_id).all()
        assert len(events_before) >= 2  # requested + (pre_approved) + approved

        demo_seed.reset(db=db, tenant_id=old_tid, confirmed=True)

        # 旧实例归档：closed 且不再 active
        old = db.query(Tenant).filter_by(id=old_tid).first()
        assert old.status == "closed"
        # 旧不可变事件仍存在且未修改
        events_after = db.query(RefundApprovalEvent).filter_by(approval_id=approval_id).all()
        assert [(e.id, e.event_type, e.detail) for e in events_before] == \
               [(e.id, e.event_type, e.detail) for e in events_after]
        assert db.query(RefundApproval).filter_by(id=approval_id).first().status == "approved"

        # 新实例严格恢复 6 订单、2 预置退款
        r2 = demo_seed.seed(db=db)
        assert r2["tenant_id"] != old_tid
        new_tid = r2["tenant_id"]
        assert _count(db, Order, new_tid) == 6
        order_ids = [o.id for o in db.query(Order).filter_by(tenant_id=new_tid).all()]
        assert db.query(RefundApproval).filter(RefundApproval.order_id.in_(order_ids)).count() == 2
        assert db.query(Tenant).filter_by(id=new_tid, status="active").first() is not None


# ═══════════════════════════════════════════════════════════
# 三、全数据幂等
# ═══════════════════════════════════════════════════════════
class TestFullIdempotency:
    def test_summary_counts_from_db_after_two_seeds(self, demo_env):
        from app.demo import demo_seed
        from app.models.audit_log import AuditLog
        from app.models.billing_invoice import BillingInvoice
        from app.models.knowledge_document import KnowledgeDocument
        from app.models.notification import Notification
        from app.models.quick_reply import QuickReply
        from app.models.session import Session
        from app.models.tenant import Tenant
        from app.models.tenant_member import TenantMember
        from app.models.usage_record import UsageRecord
        db = demo_env["db"]

        r1 = demo_seed.seed(db=db)
        r2 = demo_seed.seed(db=db)
        tid = r2["tenant_id"]

        assert _count(db, Tenant, None) >= 1 and db.query(Tenant).filter_by(id=tid, is_demo=True).count() == 1
        assert r2["members"] == 3 == _count(db, TenantMember, tid)
        assert r2["orders"] == 6 == _count(db, Order, tid)
        assert r2["tickets"] == 2 == _count(db, Ticket, tid)
        order_ids = [o.id for o in db.query(Order).filter_by(tenant_id=tid).all()]
        assert r2["refunds"] == 2 == db.query(RefundApproval).filter(RefundApproval.order_id.in_(order_ids)).count()
        assert r2["sessions"] == 2 == _count(db, Session, tid)
        assert r2["knowledge_docs"] == 7 == db.query(KnowledgeDocument).filter_by(tenant_id=tid, is_latest=True, status="ready").count()
        assert _count(db, QuickReply, tid) == 2
        assert _count(db, BillingInvoice, tid) == 2
        assert _count(db, UsageRecord, tid) == 3
        assert db.query(Notification).filter_by(tenant_id=tid, type="demo_welcome").count() == 1
        # 审计样例不重复
        assert db.query(AuditLog).filter_by(tenant_id=tid, action="demo_seed").count() == 1

    def test_restores_all_script_fields(self, demo_env):
        from app.demo import demo_seed
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]

        o3 = db.query(Order).filter_by(tenant_id=tid, order_no="ORD-DEMO-0003").first()
        o3.status = "completed"; o3.amount = 1.0; o3.paid_amount = 1.0
        o3.version = 9; o3.tracking_no = None; o3.phone = "13000000000"
        t1 = db.query(Ticket).filter_by(tenant_id=tid, ticket_no="TK-DEMO-0001").first()
        t1.status = "closed"; t1.priority = "low"; t1.description = "被篡改"; t1.category = "other"
        db.commit()

        demo_seed.seed(db=db)
        db.expire_all()
        o3 = db.query(Order).filter_by(tenant_id=tid, order_no="ORD-DEMO-0003").first()
        assert (o3.status, o3.amount, o3.paid_amount, o3.version, o3.tracking_no) == \
               ("shipped", 899.0, 899.0, 1, "SF1234567890")
        assert o3.phone is not None
        t1 = db.query(Ticket).filter_by(tenant_id=tid, ticket_no="TK-DEMO-0001").first()
        assert t1.status == "open" and t1.priority == "high" and t1.category == "general"
        assert t1.description and "被篡改" not in t1.description


# ═══════════════════════════════════════════════════════════
# 四、密码与身份隔离
# ═══════════════════════════════════════════════════════════
class TestPasswordAndIdentity:
    def test_no_random_password_when_accounts_exist(self, demo_env, monkeypatch, capsys):
        """已有账号且未 reset-passwords：不得生成/打印未生效的随机密码。"""
        from app.demo import demo_seed
        db = demo_env["db"]
        monkeypatch.delenv("DEMO_ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("DEMO_AGENT_PASSWORD", raising=False)
        demo_seed.seed(db=db)  # 首次创建（打印一次随机密码）
        capsys.readouterr()
        demo_seed.seed(db=db)  # 幂等：账号已存在，不生成随机密码
        out = capsys.readouterr().out
        assert "已随机生成" not in out

    def test_phone_occupied_by_non_demo_user_fail_closed(self, demo_env):
        """固定手机号被非演示租户用户占用：seed 必须失败且不修改该用户密码。"""
        from app.core.crypto import phone_hash
        from app.core.security import hash_password
        from app.demo import demo_seed
        from app.models.tenant_member import TenantMember
        from app.models.user import User
        db = demo_env["db"]
        other = tenant_crud.create_tenant(db, name="非演示企业", plan_type="basic")
        original_hash = hash_password("KeepMe@12345")
        user = User(phone=demo_seed.DEMO_ADMIN_PHONE, phone_hash=phone_hash(demo_seed.DEMO_ADMIN_PHONE),
                    password_hash=original_hash, role_default="admin")
        db.add(user); db.flush()
        db.add(TenantMember(tenant_id=other.id, user_id=user.id, role="admin"))
        db.commit()

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.seed(db=db)
        db.expire_all()
        assert db.query(User).filter_by(id=user.id).first().password_hash == original_hash

    def test_same_name_non_demo_tenant_ignored(self, demo_env):
        """同名普通租户（is_demo=False）不得被 seed 认领。"""
        from app.demo import demo_seed
        from app.models.tenant import Tenant
        db = demo_env["db"]
        impostor = tenant_crud.create_tenant(db, name=demo_seed.DEMO_TENANT_NAME, plan_type="basic")
        impostor.is_demo = False
        db.commit()

        r = demo_seed.seed(db=db)
        demo_tenant = db.query(Tenant).filter_by(id=r["tenant_id"]).first()
        assert demo_tenant.is_demo is True
        assert demo_tenant.id != impostor.id
        assert _count(db, Order, impostor.id) == 0  # 普通同名租户未被写入

    def test_reset_passwords_verifies_membership_and_role(self, demo_env):
        """reset-passwords 前必须证明用户属于演示租户且角色匹配。"""
        from app.crud import user as user_crud
        from app.demo import demo_seed
        from app.models.tenant_member import TenantMember
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        # 演示 agent2 的成员关系被移到非演示租户 → 重置密码必须拒绝
        other = tenant_crud.create_tenant(db, name="路人企业", plan_type="basic")
        agent2 = user_crud.get_by_phone(db, demo_seed.DEMO_AGENT2_PHONE)
        member = db.query(TenantMember).filter_by(user_id=agent2.id).first()
        member.tenant_id = other.id
        db.commit()
        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed._reset_demo_passwords(db, tid, DEMO_ADMIN_PASSWORD, DEMO_AGENT_PASSWORD)


# ═══════════════════════════════════════════════════════════
# 五、知识失败与 reset 清理
# ═══════════════════════════════════════════════════════════
class TestKnowledgeFailureAndCleanup:
    def test_wait_timeout_marks_doc_failed_with_error(self, demo_env):
        from app.crud import knowledge as knowledge_crud
        from app.demo import demo_seed
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        doc = knowledge_crud.create(db, tenant_id=tid, filename="卡死文档.md", file_type="md", file_size=1, version=1)
        db.flush(); db.commit()

        status = demo_seed._wait_doc_ready(db, doc.id, tid, timeout_seconds=0)
        assert status == "failed"
        db.expire_all()
        d = knowledge_crud.get_by_id(db, doc.id, tid)
        assert d.status == "failed"
        assert d.error_message  # 记录失败原因

    def test_reset_cleans_upload_files(self, demo_env, monkeypatch, tmp_path):
        from app.config import settings
        from app.crud import knowledge as knowledge_crud
        from app.demo import demo_seed
        from app.models.knowledge_document import KnowledgeDocument
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        monkeypatch.setattr(settings, "upload_storage_dir", str(tmp_path))
        # 归档租户自己的上传文件（按真实 doc_id 前缀）
        doc = db.query(KnowledgeDocument).filter_by(tenant_id=tid).first()
        upload_file = tmp_path / f"{doc.id}_退款政策.md"
        upload_file.write_bytes(b"x")
        demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        assert not upload_file.exists()

    def test_vector_cleanup_failure_reported(self, demo_env, monkeypatch):
        from app.demo import demo_seed
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]

        def boom(*args, **kwargs):
            raise RuntimeError("milvus down")
        from app.core import embedding_client
        monkeypatch.setattr(embedding_client, "get_milvus_collection", boom)
        monkeypatch.setattr(embedding_client, "get_es_client", boom)
        result = demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        assert result["vector_cleanup"] in ("partial", "failed")

    def test_worker_skips_non_processing_docs(self, demo_env, monkeypatch):
        """reset 将 pending 置 failed 后，旧 worker 不得重新处理/写入向量。"""
        from app.crud import knowledge as knowledge_crud
        from app.demo import demo_seed
        from app.tasks import knowledge_tasks
        db = demo_env["db"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        doc = knowledge_crud.create(db, tenant_id=tid, filename="旧任务.md", file_type="md", file_size=1, version=1)
        db.flush(); db.commit()
        knowledge_crud.update_status(db, doc, "failed", error_message="reset 清理")
        db.commit()

        called = []
        monkeypatch.setattr(knowledge_tasks, "_index_to_es", lambda *a, **k: called.append(1))
        monkeypatch.setattr(knowledge_tasks, "_index_to_milvus", lambda *a, **k: called.append(1))
        knowledge_tasks.process_document(doc.id, tid)
        assert called == []
        d = knowledge_crud.get_by_id(db, doc.id, tid)
        assert d.status == "failed"
