"""
backend/tests/contracts/test_billing_plan.py

职责: 套餐配额矩阵（fresh-eyes ，PRD 3.9）。
契约:
  - basic 租户配额 2000 / pro 10000 / 未知套餐降级 trial 100
  - incr_usage 支持自定义 quota，超限/预警按该 quota 判定
"""

from app.models.tenant import Tenant
from app.services import billing_service


def _mk_tenant(db, plan_type: str):
    t = Tenant(name=f"plan-{plan_type}", plan_type=plan_type, status="active")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


class TestPlanQuota:
    def test_get_tenant_quota_by_plan(self, db):
        t = _mk_tenant(db, "basic")
        assert billing_service.get_tenant_quota(db, t.id) == 2000

    def test_get_tenant_quota_pro(self, db):
        t = _mk_tenant(db, "pro")
        assert billing_service.get_tenant_quota(db, t.id) == 10000

    def test_get_tenant_quota_unknown_falls_back(self, db):
        t = _mk_tenant(db, "legacy")
        assert billing_service.get_tenant_quota(db, t.id) == 100

    def test_check_quota_with_plan(self, db, fake_redis):
        t = _mk_tenant(db, "basic")
        res = billing_service.check_quota(t.id, quota=billing_service.get_tenant_quota(db, t.id))
        assert res["quota"] == 2000
        assert res["remaining"] == 2000

    def test_incr_usage_custom_quota_not_overage(self, fake_redis):
        res = billing_service.incr_usage(tenant_id=1, quota=2000)
        assert res["quota"] == 2000
        assert res["is_overage"] is False

    def test_incr_usage_overage_at_quota(self, fake_redis):
        for _ in range(100):
            billing_service.incr_usage(tenant_id=2, quota=100)
        res = billing_service.incr_usage(tenant_id=2, quota=100)
        assert res["is_overage"] is True
