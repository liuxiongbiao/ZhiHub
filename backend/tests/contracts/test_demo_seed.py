"""契约测试： 电商演示租户 seed（幂等 / 重置 / 环境限制 / 租户隔离 / API 可查）。"""

import pytest
from sqlalchemy import text

from app.crud import tenant as tenant_crud
from app.models.order import Order
from app.models.ticket import Ticket

DEMO_ADMIN_PASSWORD = "DemoAdmin@12345"
DEMO_AGENT_PASSWORD = "DemoAgent@12345"


@pytest.fixture()
def demo_env(monkeypatch, db):
    """注入固定演示密码 + fake 知识上传（返回 ready 文档，模拟真实处理完成）。"""
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", DEMO_ADMIN_PASSWORD)
    monkeypatch.setenv("DEMO_AGENT_PASSWORD", DEMO_AGENT_PASSWORD)

    from app.crud import knowledge as knowledge_crud

    upload_calls = []

    def fake_upload(db_, *, file, tenant_id, expires_days=None):
        upload_calls.append(file.filename)
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
    return {"upload_calls": upload_calls, "db": db}


def _count(db, model, tenant_id=None):
    q = db.query(model)
    if tenant_id is not None:
        q = q.filter(model.tenant_id == tenant_id)
    return q.count()


class TestDemoSeed:
    def test_seed_idempotent_two_runs(self, demo_env):
        from app.demo import demo_seed
        db = demo_env["db"]

        # 对齐真实 MySQL：强制启用 SQLite 外键，覆盖 CSAT -> message 的清理顺序。
        db.execute(text("PRAGMA foreign_keys=ON"))

        r1 = demo_seed.seed(db=db)
        r2 = demo_seed.seed(db=db)
        assert r1["tenant_id"] == r2["tenant_id"]

        tid = r1["tenant_id"]
        assert _count(db, Order, tid) == 6
        assert _count(db, Ticket, tid) == 2
        # 两次执行后数量不重复：同一租户数据量保持不变
        assert _count(db, Order, tid) == 6
        # 用户/成员不重复：1 管理员 + 2 客服
        from app.models.user import User
        from app.models.tenant_member import TenantMember
        demo_users = db.query(User).filter(User.phone.like("139%")).count()
        assert _count(db, TenantMember, tid) == 3

    def test_reset_then_seed_restores(self, demo_env):
        from app.demo import demo_seed
        from app.models.tenant import Tenant
        db = demo_env["db"]
        r1 = demo_seed.seed(db=db)
        tid = r1["tenant_id"]

        # 归档语义：reset 关闭旧实例，create 得到全新实例
        demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        assert db.query(Tenant).filter_by(id=tid).first().status == "closed"

        r2 = demo_seed.seed(db=db)
        assert r2["tenant_id"] != tid
        new_tid = r2["tenant_id"]
        assert _count(db, Order, new_tid) == 6
        assert _count(db, Ticket, new_tid) == 2
        # 新实例订单状态恢复（0003 已发货 + 物流单号）
        assert db.query(Order).filter_by(tenant_id=new_tid, order_no="ORD-DEMO-0003").first().status == "shipped"

    def test_production_rejected(self, demo_env, monkeypatch):
        from app.config import settings
        from app.demo import demo_seed
        monkeypatch.setattr(settings, "app_env", "production")
        with pytest.raises(demo_seed.DemoSeedEnvError):
            demo_seed.seed(db=demo_env["db"])

    def test_does_not_touch_other_tenants(self, demo_env):
        from app.demo import demo_seed
        db = demo_env["db"]
        other = tenant_crud.create_tenant(db, name="其他租户", plan_type="basic")
        db.add(Order(tenant_id=other.id, order_no="ORD-OTHER-0001", amount=1.0, paid_amount=1.0, status="paid", phone="13800000001"))
        db.commit()

        r = demo_seed.seed(db=db)
        # 其他租户订单仍在且归原租户
        o = db.query(Order).filter_by(order_no="ORD-OTHER-0001").first()
        assert o is not None and o.tenant_id == other.id
        # 其他租户无任何 demo 数据
        assert _count(db, Order, other.id) == 1
        assert _count(db, Ticket, other.id) == 0

    def test_demo_order_no_conflict_with_other_tenant_rejected(self, demo_env):
        """demo 单号被其他租户占用时：报错且不修改他人数据（禁止 e2e-seed 式抢单号）。"""
        from app.demo import demo_seed
        db = demo_env["db"]
        other = tenant_crud.create_tenant(db, name="占用者", plan_type="basic")
        stolen = Order(tenant_id=other.id, order_no="ORD-DEMO-0002", amount=1.0, paid_amount=1.0, status="paid", phone="13800000001")
        db.add(stolen)
        db.commit()

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.seed(db=db)
        db.refresh(stolen)
        assert stolen.tenant_id == other.id  # 未被认领/修改

    def test_reset_passwords_flag_updates_demo_accounts(self, demo_env):
        from app.core.security import verify_password
        from app.crud import user as user_crud
        from app.demo import demo_seed
        db = demo_env["db"]
        demo_seed.seed(db=db)
        # 默认幂等不动密码；显式 reset_passwords 才更新
        demo_seed.seed(db=db, admin_password="NewAdmin@12345", agent_password="NewAgent@12345", reset_passwords=True)
        admin = user_crud.get_by_phone(db, demo_seed.DEMO_ADMIN_PHONE)
        assert verify_password("NewAdmin@12345", admin.password_hash)
        agent = user_crud.get_by_phone(db, demo_seed.DEMO_AGENT1_PHONE)
        assert verify_password("NewAgent@12345", agent.password_hash)

    def test_reset_requires_matching_demo_tenant(self, demo_env):
        from app.demo import demo_seed
        db = demo_env["db"]
        other = tenant_crud.create_tenant(db, name="路人租户", plan_type="basic")
        db.commit()
        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.reset(db=db, tenant_id=other.id, confirmed=True)

    def test_knowledge_upload_failure_marks_failed_and_raises(self, demo_env, monkeypatch):
        from app.demo import demo_seed
        db = demo_env["db"]

        def failing_upload(db_, *, file, tenant_id, expires_days=None):
            raise RuntimeError("simulated upload failure")

        monkeypatch.setattr(demo_seed, "_upload_knowledge", failing_upload)
        with pytest.raises(demo_seed.DemoSeedKnowledgeError):
            demo_seed.seed(db=db)
        # 不得伪装 ready：demo 租户知识文档数为 0（失败即 failed/无记录）
        from app.models.knowledge_document import KnowledgeDocument
        assert _count(db, KnowledgeDocument) == 0

    def test_data_queryable_via_api(self, demo_env, client, make_token):
        from app.demo import demo_seed
        db = demo_env["db"]
        r = demo_seed.seed(db=db)
        tid = r["tenant_id"]
        token = make_token(tenant_id=tid, role="admin")
        headers = {"Authorization": f"Bearer {token}"}

        orders = client.get("/api/v1/order/list", headers=headers)
        assert orders.status_code == 200
        assert any(o["order_no"] == "ORD-DEMO-0002" for o in orders.json()["items"])
        tickets = client.get("/api/v1/ticket/list", headers=headers)
        assert tickets.status_code == 200
        assert any(t["ticket_no"] == "TK-DEMO-0001" for t in tickets.json()["items"])
        sessions = client.get("/api/v1/session/list", headers=headers)
        assert sessions.status_code == 200
        assert any(s["status"] == "waiting_human" for s in sessions.json()["items"])
        qrs = client.get("/api/v1/session/quick-replies", headers=headers)
        assert qrs.status_code == 200 and len(qrs.json()) >= 2
        kb = client.get("/api/v1/knowledge/list", headers=headers)
        assert kb.status_code == 200
        assert len(kb.json()["items"]) >= 7  # 7 份演示知识（特殊赔偿问题故意不建文档）
        assert all(d["status"] == "ready" for d in kb.json()["items"])
