"""契约测试： 演示租户收尾（跨租户隔离 / legacy 认领 / reset 原子性与归档值 /
幂等 create 恢复演示字段）。

每条测试先于产品代码编写（TDD）：本文件中的断言描述  要求的行为。
"""

import re

import pytest

from app.crud import tenant as tenant_crud
from app.models.order import Order
from app.models.ticket import Ticket

DEMO_ADMIN_PASSWORD = "DemoAdmin@12345"
DEMO_AGENT_PASSWORD = "DemoAgent@12345"

_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


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
    return {"db": db, "demo_seed": demo_seed}


def _count(db, model, tenant_id=None):
    q = db.query(model)
    if tenant_id is not None:
        q = q.filter(model.tenant_id == tenant_id)
    return q.count()


def _add_normal_membership(db, user, role="agent"):
    """把用户加进一个普通（非 demo）租户，返回租户。"""
    from app.models.tenant_member import TenantMember

    other = tenant_crud.create_tenant(db, name=f"普通企业{user.id}", plan_type="basic")
    db.add(TenantMember(tenant_id=other.id, user_id=user.id, role=role))
    db.commit()
    return other


# ═══════════════════════════════════════════════════════════
# 一、固定演示账号跨租户隔离（fail-closed）
# ═══════════════════════════════════════════════════════════
class TestCrossTenantIsolation:
    def test_mixed_membership_fail_closed_on_seed(self, demo_env):
        """同一用户同时属于 demo 与普通租户 -> seed 必须 fail-closed。"""
        from app.crud import user as user_crud

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        demo_seed.seed(db=db)
        admin = user_crud.get_by_phone(db, demo_seed.DEMO_ADMIN_PHONE)
        _add_normal_membership(db, admin)

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.seed(db=db)

    def test_mixed_membership_fail_closed_on_agent_phone(self, demo_env):
        """客服固定手机号的混合归属同样 fail-closed。"""
        from app.crud import user as user_crud

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        demo_seed.seed(db=db)
        agent1 = user_crud.get_by_phone(db, demo_seed.DEMO_AGENT1_PHONE)
        _add_normal_membership(db, agent1)

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.seed(db=db)

    def test_reset_passwords_rejects_mixed_membership(self, demo_env):
        """密码重置遇混合归属必须拒绝，且不修改任何演示账号密码。"""
        from app.core.security import verify_password
        from app.crud import user as user_crud

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        agent1 = user_crud.get_by_phone(db, demo_seed.DEMO_AGENT1_PHONE)
        original_hash = agent1.password_hash
        _add_normal_membership(db, agent1)

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed._reset_demo_passwords(db, tid, DEMO_ADMIN_PASSWORD, "MixedFail@12345")
        db.expire_all()
        assert user_crud.get_by_phone(db, demo_seed.DEMO_AGENT1_PHONE).password_hash == original_hash

    def test_reset_passwords_rejects_wrong_role(self, demo_env):
        """成员角色与固定身份不匹配 -> 拒绝重置密码。"""
        from app.models.tenant_member import TenantMember
        from app.crud import user as user_crud

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        admin = user_crud.get_by_phone(db, demo_seed.DEMO_ADMIN_PHONE)
        member = db.query(TenantMember).filter_by(tenant_id=tid, user_id=admin.id).first()
        member.role = "agent"
        db.commit()

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed._reset_demo_passwords(db, tid, DEMO_ADMIN_PASSWORD, DEMO_AGENT_PASSWORD)

    def test_reset_passwords_rejects_non_active_demo_tenant(self, demo_env):
        """当前演示租户已 closed（归档）-> 不再属于 active demo tenant，拒绝重置。"""
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        db.query(Tenant).filter_by(id=tid).update({"status": "closed"})
        db.commit()

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed._reset_demo_passwords(db, tid, DEMO_ADMIN_PASSWORD, DEMO_AGENT_PASSWORD)

    def test_reset_keeps_phone_of_mixed_membership_user(self, demo_env):
        """归档释放手机号前执行排他检查：混合归属用户的手机号保持原状（不破坏普通租户登录）。"""
        from app.crud import user as user_crud

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        agent1 = user_crud.get_by_phone(db, demo_seed.DEMO_AGENT1_PHONE)
        _add_normal_membership(db, agent1)

        demo_seed.reset(db=db, tenant_id=tid, confirmed=True)

        db.expire_all()
        # 混合归属用户手机号未被改动
        still = user_crud.get_by_phone(db, demo_seed.DEMO_AGENT1_PHONE)
        assert still is not None and still.id == agent1.id
        # 其余两个演示账号正常释放（get_by_phone 查不到固定号）
        assert user_crud.get_by_phone(db, demo_seed.DEMO_ADMIN_PHONE) is None
        assert user_crud.get_by_phone(db, demo_seed.DEMO_AGENT2_PHONE) is None


# ═══════════════════════════════════════════════════════════
# 二、legacy 演示租户认领收紧
# ═══════════════════════════════════════════════════════════
class TestLegacyClaim:
    def _make_impostor(self, db, demo_seed, *, admin_role="admin"):
        """同名普通租户 + 固定管理员手机号成员（可疑租户）。"""
        from app.core.crypto import phone_hash
        from app.core.security import hash_password
        from app.models.tenant_member import TenantMember
        from app.models.user import User

        impostor = tenant_crud.create_tenant(db, name=demo_seed.DEMO_TENANT_NAME, plan_type="basic")
        impostor.is_demo = False
        user = User(phone=demo_seed.DEMO_ADMIN_PHONE,
                    phone_hash=phone_hash(demo_seed.DEMO_ADMIN_PHONE),
                    password_hash=hash_password("Impostor@12345"), role_default="admin")
        db.add(user)
        db.flush()
        db.add(TenantMember(tenant_id=impostor.id, user_id=user.id, role=admin_role))
        db.commit()
        return impostor, user

    def test_create_does_not_claim_same_name_tenant(self, demo_env):
        """普通 create 不自动认领同名 + 固定手机号成员的租户（fail-closed 报错）。"""
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        impostor, _user = self._make_impostor(db, demo_seed)
        name_before = impostor.name

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.seed(db=db)

        db.expire_all()
        fresh = db.query(Tenant).filter_by(id=impostor.id).first()
        assert fresh.is_demo is not True
        assert fresh.name == name_before
        assert fresh.status == "active"

    def test_claim_legacy_rejects_incomplete_identity_set(self, demo_env):
        """显式 claim-legacy 也必须校验完整演示身份集合与角色（仅 admin 成员不够）。"""
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        impostor, _user = self._make_impostor(db, demo_seed)

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.seed(db=db, claim_legacy=True)

        db.expire_all()
        assert db.query(Tenant).filter_by(id=impostor.id).first().is_demo is not True

    def test_claim_legacy_claims_pre_d34_instance(self, demo_env):
        """d34 之前创建的完整旧演示实例：显式 claim-legacy 可认领（tenant_id 不变）。"""
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        db.query(Tenant).filter_by(id=tid).update({"is_demo": False})  # 模拟 d34 前旧实例
        db.commit()

        r2 = demo_seed.seed(db=db, claim_legacy=True)
        assert r2["tenant_id"] == tid
        db.expire_all()
        assert db.query(Tenant).filter_by(id=tid).first().is_demo is True

    def test_default_create_does_not_claim_pre_d34_instance(self, demo_env):
        """不带 claim-legacy 时不认领旧实例（固定手机号被非 demo 归属占用 -> fail-closed）。"""
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        db.query(Tenant).filter_by(id=tid).update({"is_demo": False})
        db.commit()

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.seed(db=db)
        db.expire_all()
        assert db.query(Tenant).filter_by(id=tid).first().is_demo is not True


# ═══════════════════════════════════════════════════════════
# 三、reset 原子性（单事务，中途故障全量 rollback）
# ═══════════════════════════════════════════════════════════
class TestResetAtomicity:
    def test_midway_failure_rolls_back_everything(self, demo_env, monkeypatch):
        """中途故障注入：租户名/状态、手机号、单号、文档全部保持原状。"""
        from app.crud import user as user_crud
        from app.models.knowledge_document import KnowledgeDocument
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        name_before = db.query(Tenant).filter_by(id=tid).first().name
        order_nos_before = sorted(o.order_no for o in db.query(Order).filter_by(tenant_id=tid).all())
        docs_before = _count(db, KnowledgeDocument, tid)

        def boom(db_, tenant_id_):
            raise RuntimeError("注入：归档中途故障")

        monkeypatch.setattr(demo_seed, "_archive_demo_order_nos", boom)
        with pytest.raises(RuntimeError):
            demo_seed.reset(db=db, tenant_id=tid, confirmed=True)

        db.expire_all()
        tenant = db.query(Tenant).filter_by(id=tid).first()
        assert tenant.status == "active"
        assert tenant.name == name_before
        # 固定手机号未释放
        assert user_crud.get_by_phone(db, demo_seed.DEMO_ADMIN_PHONE) is not None
        assert user_crud.get_by_phone(db, demo_seed.DEMO_AGENT1_PHONE) is not None
        # 单号未归档改名
        assert sorted(o.order_no for o in db.query(Order).filter_by(tenant_id=tid).all()) == order_nos_before
        # 文档未删除
        assert _count(db, KnowledgeDocument, tid) == docs_before


# ═══════════════════════════════════════════════════════════
# 四、reset 状态检查与归档值
# ═══════════════════════════════════════════════════════════
class TestResetStateAndArchiveValues:
    def test_second_reset_returns_already_archived_without_modification(self, demo_env):
        """重复 reset：already_archived，租户名/归档单号/占位号不再变化（不无限增长）。"""
        from app.crud import user as user_crud
        from app.models.tenant import Tenant
        from app.models.tenant_member import TenantMember
        from app.models.user import User

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        db.expire_all()

        name1 = db.query(Tenant).filter_by(id=tid).first().name
        nos1 = sorted(o.order_no for o in db.query(Order).filter_by(tenant_id=tid).all())
        member_uids = [m.user_id for m in db.query(TenantMember).filter_by(tenant_id=tid).all()]
        phones1 = {u.id: u.phone for u in db.query(User).filter(User.id.in_(member_uids)).all()}

        r2 = demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        assert r2.get("already_archived") is True

        db.expire_all()
        assert db.query(Tenant).filter_by(id=tid).first().name == name1
        assert sorted(o.order_no for o in db.query(Order).filter_by(tenant_id=tid).all()) == nos1
        phones2 = {u.id: u.phone for u in db.query(User).filter(User.id.in_(member_uids)).all()}
        assert phones1 == phones2

    def test_reset_rejects_readonly_demo_tenant(self, demo_env):
        """非 active（readonly）演示租户不允许 reset。"""
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        db.query(Tenant).filter_by(id=tid).update({"status": "readonly"})
        db.commit()

        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed.reset(db=db, tenant_id=tid, confirmed=True)

    def test_archived_phones_are_valid_unique_verifiable_11digit(self, demo_env):
        """归档占位号：合法中国手机号格式、11 位、互不相同、可用 get_by_phone 反查。"""
        from app.crud import user as user_crud
        from app.models.tenant_member import TenantMember
        from app.models.user import User

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        db.expire_all()

        member_uids = [m.user_id for m in db.query(TenantMember).filter_by(tenant_id=tid).all()]
        users = db.query(User).filter(User.id.in_(member_uids)).all()
        assert len(users) == 3
        phones = [u.phone for u in users]
        for p in phones:
            assert len(p) == 11, f"占位号必须 11 位: {p!r}"
            assert _CN_MOBILE_RE.match(p), f"占位号必须是合法手机号: {p!r}"
            # 可验证：能按占位号反查到用户
            assert user_crud.get_by_phone(db, p) is not None
        assert len(set(phones)) == 3  # 互不相同

    def test_archived_phone_collision_handled(self, demo_env):
        """占位号碰撞：目标号已被其他用户占用时必须换号且保持全局唯一。"""
        from app.core.crypto import phone_hash
        from app.core.security import hash_password
        from app.models.user import User

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]

        # 先生成一次拿走首选项，再占用它
        first = demo_seed._generate_archived_phone(db, tenant_id=tid, index=0)
        db.add(User(phone=first, phone_hash=phone_hash(first),
                    password_hash=hash_password("Squatter@12345"), role_default="agent"))
        db.commit()

        second = demo_seed._generate_archived_phone(db, tenant_id=tid, index=0)
        assert second != first
        assert len(second) == 11 and _CN_MOBILE_RE.match(second)
        assert phone_hash(second) != phone_hash(first)

    def test_archived_order_no_within_column_length(self, demo_env):
        """归档单号长度受 String(30) 列约束：超长租户 id 必须报错而非截断溢出。"""
        from app.crud import user as user_crud  # noqa: F401

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        # 常规租户 id：归档号 <= 30
        tid = demo_seed.seed(db=db)["tenant_id"]
        demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        for o in db.query(Order).filter_by(tenant_id=tid).all():
            assert len(o.order_no) <= 30
        for t in db.query(Ticket).filter_by(tenant_id=tid).all():
            assert len(t.ticket_no) <= 30

        # 构造超长租户 id（无 FK 强制）→ 归档必须拒绝
        huge = 10 ** 13
        db.add(Order(tenant_id=huge, order_no="ORD-DEMO-0009", amount=1.0,
                     paid_amount=1.0, status="paid", phone="13900001111"))
        db.commit()
        with pytest.raises(demo_seed.DemoSeedConflictError):
            demo_seed._archive_demo_order_nos(db, huge)

    def test_reset_reports_active_and_archived_counts(self, demo_env):
        """reset 与 create summary 必须报告 active / archived 演示实例数量。"""
        from app.models.tenant import Tenant

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]
        r = demo_seed.reset(db=db, tenant_id=tid, confirmed=True)
        assert r["demo_tenants_active"] == 0
        assert r["demo_tenants_archived"] == 1

        r2 = demo_seed.seed(db=db)
        assert r2["demo_tenants_active"] == 1
        assert r2["demo_tenants_archived"] == 1


# ═══════════════════════════════════════════════════════════
# 五、幂等 create 恢复演示基线字段
# ═══════════════════════════════════════════════════════════
class TestIdempotentRestore:
    def test_create_restores_tampered_demo_fields(self, demo_env):
        """重复 create 恢复全部演示字段（created_by / payment_trade_no / assignee /
        快捷回复内容 / 账单 / 用量），而不只是按唯一键跳过。"""
        from app.models.billing_invoice import BillingInvoice
        from app.models.quick_reply import QuickReply
        from app.models.usage_record import UsageRecord

        db, demo_seed = demo_env["db"], demo_env["demo_seed"]
        tid = demo_seed.seed(db=db)["tenant_id"]

        # 篡改演示字段
        o2 = db.query(Order).filter_by(tenant_id=tid, order_no="ORD-DEMO-0002").first()
        o2.payment_trade_no = "TAMPER-TRADE"
        o2.created_by = "tampered"
        t1 = db.query(Ticket).filter_by(tenant_id=tid, ticket_no="TK-DEMO-0001").first()
        t1.assignee_id = 999
        t1.created_by = "tampered"
        qr = db.query(QuickReply).filter_by(tenant_id=tid, shortcut="chaxun").first()
        qr.content = "被篡改的快捷回复"
        inv = db.query(BillingInvoice).filter_by(tenant_id=tid, period="2026-08").first()
        inv.status = "paid"
        inv.base_fee = 1.0
        inv.overage_fee = 999.0
        ur = db.query(UsageRecord).filter_by(tenant_id=tid, date=__import__("datetime").date(2026, 8, 15)).first()
        ur.conversation_count = 0
        ur.is_overage = False
        db.commit()

        demo_seed.seed(db=db)
        db.expire_all()

        o2 = db.query(Order).filter_by(tenant_id=tid, order_no="ORD-DEMO-0002").first()
        assert o2.payment_trade_no == "PAY-DEMO-ORD-DEMO-0002"
        assert o2.created_by != "tampered"
        t1 = db.query(Ticket).filter_by(tenant_id=tid, ticket_no="TK-DEMO-0001").first()
        assert t1.assignee_id is None
        assert t1.created_by != "tampered"
        qr = db.query(QuickReply).filter_by(tenant_id=tid, shortcut="chaxun").first()
        assert qr.content == "您好，正在为您查询，请稍候。"
        inv = db.query(BillingInvoice).filter_by(tenant_id=tid, period="2026-08").first()
        assert (inv.status, inv.base_fee, inv.overage_fee) == ("unpaid", 100.0, 12.5)
        ur = db.query(UsageRecord).filter_by(tenant_id=tid, date=__import__("datetime").date(2026, 8, 15)).first()
        assert ur.conversation_count == 12 and ur.is_overage is True
