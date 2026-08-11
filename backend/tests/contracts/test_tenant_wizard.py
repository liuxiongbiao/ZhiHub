"""
backend/tests/contracts/test_tenant_wizard.py

职责: 测试 PRD 3.1 配置向导4步推进 + 邀请坐席 + seat_limit 校验契约
对应日程: Day 2（feature 001-account-tenant 扩展）

测试内容:
  - TestWizardStateMachine: 向导状态机（basic->knowledge->mcp->team->done 逐步推进；
    done 不可再推进；当前步骤与完成度计算正确）
  - TestInviteSeatLimit: seat_limit 校验（名额内成功；超限 409 SeatLimitExceeded）
  - TestInviteAccept: 接受邀请创建 user + member(role=agent)；token 一次性，接受后失效
  - TestInviteTokenExpired: 过期/缺失 token 接受失败

设计说明（面试点）:
  - 向导状态机用显式有序列表 WIZARD_STEPS，advance 校验当前 index +1，
    杜绝跳步（如 basic 直接到 team）
  - 邀请 token 写 Redis 带 TTL，接受后 DEL 一次性失效；过期 key 自然消失
  - seat_limit 在 invite 时校验（非 accept 时），避免发了邀请却无法兑现
"""

import pytest

from app.core import redis as redis_module
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud
from app.models.tenant import Tenant
from app.models.tenant_member import TenantMember
from app.services import tenant_service


# ─── 共享夹具 ───
@pytest.fixture()
def tenant(db):
    """建一个 trial 租户（seat_limit=1, wizard_step=basic），含 1 个 admin 成员。"""
    from app.core.security import hash_password

    t = Tenant(name="Acme", plan_type="trial", seat_limit=1, status="active", wizard_step="basic")
    db.add(t)
    db.flush()
    user = user_crud.create(
        db, phone="13800000000", password_hash=hash_password("abc12345"), role_default="admin"
    )
    tenant_crud.add_member(db, tenant_id=t.id, user_id=user.id, role="admin")
    db.commit()
    return t


def _set_seat_limit(db, tenant, limit):
    tenant.seat_limit = limit
    db.commit()


# ═══════════════════════════════════════════════════════════
# 配置向导状态机
# ═══════════════════════════════════════════════════════════
class TestWizardStateMachine:
    """basic -> knowledge -> mcp -> team -> done 有序推进契约。"""

    def test_wizard_initial_status(self, db, tenant):
        """新租户 wizard_step 默认 basic，completed=0。"""
        status = tenant_service.get_wizard_status(db, tenant.id)
        assert status["current_step"] == "basic"
        assert status["completed"] is False
        assert status["total_steps"] == len(tenant_service.WIZARD_STEPS)

    def test_wizard_advance_full_path(self, db, tenant):
        """basic->knowledge->mcp->team->done 逐步推进，每步 current_step 正确。"""
        expected = ["knowledge", "mcp", "team", "done"]
        for i, step in enumerate(expected):
            result = tenant_service.advance_wizard(db, tenant.id)
            assert result["current_step"] == step
            db.refresh(tenant)
            assert tenant.wizard_step == step
        # done 后 completed=True
        assert tenant_service.get_wizard_status(db, tenant.id)["completed"] is True

    def test_wizard_done_no_advance(self, db, tenant):
        """done 再推进 -> WizardAlreadyDone（映射 409）。"""
        for _ in range(4):  # basic -> done
            tenant_service.advance_wizard(db, tenant.id)
        db.refresh(tenant)
        assert tenant.wizard_step == "done"
        with pytest.raises(tenant_service.WizardAlreadyDone):
            tenant_service.advance_wizard(db, tenant.id)

    def test_wizard_status_unknown_tenant(self, db, tenant):
        """不存在的租户查向导状态 -> TenantNotFound。"""
        with pytest.raises(tenant_service.TenantNotFound):
            tenant_service.get_wizard_status(db, tenant_id=999999)


# ═══════════════════════════════════════════════════════════
# seat_limit 校验
# ═══════════════════════════════════════════════════════════
class TestInviteSeatLimit:
    """邀请时校验租户成员数是否已达 seat_limit。"""

    def test_invite_within_limit(self, db, tenant):
        """seat_limit=2 时，已有1成员，再邀请1个成功（名额内）。"""
        _set_seat_limit(db, tenant, 2)
        result = tenant_service.invite_member(db, tenant.id, phone="13900000001")
        assert "invite_token" in result
        assert result["expires_in"] > 0

    def test_invite_exceed_limit(self, db, tenant):
        """seat_limit=1 且已有1成员 -> 邀请被拒 SeatLimitExceeded（409）。"""
        _set_seat_limit(db, tenant, 1)  # 已有1个 admin 成员
        with pytest.raises(tenant_service.SeatLimitExceeded):
            tenant_service.invite_member(db, tenant.id, phone="13900000001")


# ═══════════════════════════════════════════════════════════
# 邀请接受
# ═══════════════════════════════════════════════════════════
class TestInviteAccept:
    """接受邀请 -> 创建 user + member(role=agent)，token 一次性失效。"""

    def test_invite_accept_creates_agent(self, db, tenant):
        """admin 邀请 phone=13900000002，被邀请者接受 -> 创建 user + member(role=agent)。"""
        _set_seat_limit(db, tenant, 2)
        invite = tenant_service.invite_member(db, tenant.id, phone="13900000002")

        result = tenant_service.accept_invite(
            db, invite_token=invite["invite_token"],
            phone="13900000002", password="abc12345",
        )
        assert result["role"] == "agent"
        assert result["tenant_id"] == tenant.id

        # user 已创建
        user = user_crud.get_by_phone(db, "13900000002")
        assert user is not None
        # member 关联存在且 role=agent
        member = tenant_crud.get_member(db, tenant_id=tenant.id, user_id=user.id)
        assert member is not None
        assert member.role == "agent"

    def test_invite_token_one_shot(self, db, tenant):
        """接受后 token 立即失效，再次接受 -> InvalidInviteToken。"""
        _set_seat_limit(db, tenant, 2)
        invite = tenant_service.invite_member(db, tenant.id, phone="13900000003")
        tenant_service.accept_invite(
            db, invite_token=invite["invite_token"],
            phone="13900000003", password="abc12345",
        )
        with pytest.raises(tenant_service.InvalidInviteToken):
            tenant_service.accept_invite(
                db, invite_token=invite["invite_token"],
                phone="13900000003", password="abc12345",
            )

    def test_invite_accept_phone_mismatch(self, db, tenant):
        """接受时手机号与邀请 token 绑定的手机号不一致 -> InvalidInviteToken。"""
        _set_seat_limit(db, tenant, 2)
        invite = tenant_service.invite_member(db, tenant.id, phone="13900000004")
        with pytest.raises(tenant_service.InvalidInviteToken):
            tenant_service.accept_invite(
                db, invite_token=invite["invite_token"],
                phone="13900000099", password="abc12345",
            )


# ═══════════════════════════════════════════════════════════
# token 过期
# ═══════════════════════════════════════════════════════════
class TestInviteTokenExpired:
    """过期/缺失的邀请 token 接受失败。"""

    def test_invite_token_expired(self, db, tenant, fake_redis):
        """token 过期（模拟 key 自然消失）后接受 -> InvalidInviteToken。"""
        _set_seat_limit(db, tenant, 2)
        invite = tenant_service.invite_member(db, tenant.id, phone="13900000005")
        # 模拟 token 过期：Redis key 自然消失（fakeredis 已过 TTL）
        # 这里手动 DEL 模拟过期后的状态
        for key in fake_redis.scan_iter("invite:*"):
            fake_redis.delete(key)
        with pytest.raises(tenant_service.InvalidInviteToken):
            tenant_service.accept_invite(
                db, invite_token=invite["invite_token"],
                phone="13900000005", password="abc12345",
            )

    def test_invite_accept_unknown_token(self, db, tenant):
        """完全不存在的 token 接受 -> InvalidInviteToken。"""
        with pytest.raises(tenant_service.InvalidInviteToken):
            tenant_service.accept_invite(
                db, invite_token="not-a-real-token",
                phone="13900000006", password="abc12345",
            )
