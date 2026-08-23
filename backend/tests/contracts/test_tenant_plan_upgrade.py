"""
backend/tests/contracts/test_tenant_plan_upgrade.py

职责: 验证套餐目录、即时升级、企业版待议价、权限与计费配额联动（PRD §3.9）。
"""

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.services import billing_service, tenant_service


def _tenant_with_admin(db, *, plan_type: str = "trial"):
    """创建带管理员成员的租户，供服务与接口契约复用。"""

    tenant = Tenant(
        name=f"plan-{plan_type}",
        plan_type=plan_type,
        status="active",
        seat_limit=tenant_service.PLAN_CATALOG[plan_type]["seat_limit"],
    )
    db.add(tenant)
    db.flush()
    user = user_crud.create(
        db,
        phone=f"139{tenant.id:08d}"[-11:],
        password_hash=hash_password("abc12345"),
        role_default="admin",
    )
    tenant_crud.add_member(db, tenant_id=tenant.id, user_id=user.id, role="admin")
    db.commit()
    return tenant, user


class TestPlanUpgradeService:
    """套餐升级服务的状态、限制联动与审计契约。"""

    def test_basic_upgrade_effective_immediately(self, db, fake_redis):
        """试用版升级基础版后，套餐、席位、配额与计费口径立即一致。"""

        tenant, user = _tenant_with_admin(db)
        result = tenant_service.change_plan(db, tenant.id, "basic", user_id=user.id)
        db.refresh(tenant)
        assert result["change_status"] == "effective"
        assert tenant.plan_type == "basic"
        assert tenant.seat_limit == 2
        assert tenant.quota_conversations_per_month == 2000
        assert billing_service.get_tenant_quota(db, tenant.id) == 2000
        audit = db.execute(
            select(AuditLog).where(AuditLog.action == "plan_upgraded")
        ).scalar_one()
        assert "from=trial;to=basic" in audit.detail

    def test_downgrade_and_same_plan_rejected(self, db):
        """套餐接口只允许向上升级，拒绝同级、降级与回到试用版。"""

        tenant, user = _tenant_with_admin(db, plan_type="pro")
        for target in ("pro", "basic", "trial", "unknown"):
            with pytest.raises(tenant_service.InvalidPlanChange):
                tenant_service.change_plan(db, tenant.id, target, user_id=user.id)

    def test_enterprise_request_keeps_current_plan(self, db):
        """企业版申请仅登记待议价目标，不中断当前套餐服务。"""

        tenant, user = _tenant_with_admin(db, plan_type="basic")
        result = tenant_service.change_plan(db, tenant.id, "enterprise", user_id=user.id)
        db.refresh(tenant)
        assert result["change_status"] == "pending_negotiation"
        assert tenant.plan_type == "basic"
        assert tenant.pending_plan_type == "enterprise"
        assert tenant.status == "active"

    def test_enterprise_approval_activates_pending_plan(self, db):
        """平台审批后企业版生效并清除待议价标记。"""

        tenant, user = _tenant_with_admin(db, plan_type="pro")
        tenant_service.change_plan(db, tenant.id, "enterprise", user_id=user.id)
        result = tenant_service.approve_enterprise(db, tenant.id)
        db.refresh(tenant)
        assert result["status"] == "active"
        assert tenant.plan_type == "enterprise"
        assert tenant.pending_plan_type is None
        assert tenant.quota_conversations_per_month == 100000


class TestPlanUpgradeApi:
    """套餐升级接口的管理员权限与响应契约。"""

    def test_admin_can_list_and_upgrade(self, client, db, make_token):
        """管理员可查询套餐卡片并升级至基础版。"""

        tenant, user = _tenant_with_admin(db)
        headers = {"Authorization": f"Bearer {make_token(user.id, tenant.id, 'admin')}"}
        catalog = client.get("/api/v1/tenant/plans", headers=headers)
        assert catalog.status_code == 200
        assert len(catalog.json()["plans"]) == 4
        response = client.put(
            "/api/v1/tenant/plan", json={"target_plan": "basic"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["current_plan"] == "basic"

    def test_agent_cannot_change_plan(self, client, db, make_token):
        """坐席角色即使直接调用接口也会被后端拒绝。"""

        tenant, user = _tenant_with_admin(db)
        headers = {"Authorization": f"Bearer {make_token(user.id, tenant.id, 'agent')}"}
        response = client.put(
            "/api/v1/tenant/plan", json={"target_plan": "basic"}, headers=headers
        )
        assert response.status_code == 403
