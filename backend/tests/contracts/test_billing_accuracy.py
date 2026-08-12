"""契约测试：P0-2/P0-3 计费准确性（specs/015-billing-accuracy）。"""

import pytest

from app.models.tenant import Tenant
from app.services import billing_service
from app.config import settings


def _mk_tenant(db, plan_type: str, *, status: str = "active") -> Tenant:
    t = Tenant(name=f"plan-{plan_type}", plan_type=plan_type, status=status)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


class TestUsageQuotaByPlan:
    """P0-2：incr_usage_for_tenant 必须按租户套餐配额判定超额。"""

    def test_basic_tenant_not_overage_until_2000(self, db, fake_redis):
        t = _mk_tenant(db, "basic")
        for _ in range(100):
            res = billing_service.incr_usage_for_tenant(db, t.id)
        assert res["quota"] == 2000
        assert res["is_overage"] is False

    def test_basic_tenant_overage_at_quota(self, db, fake_redis):
        t = _mk_tenant(db, "basic")
        for _ in range(2000):
            billing_service.incr_usage_for_tenant(db, t.id)
        res = billing_service.incr_usage_for_tenant(db, t.id)
        assert res["quota"] == 2000
        assert res["is_overage"] is True

    def test_trial_still_100(self, db, fake_redis):
        t = _mk_tenant(db, "trial")
        for _ in range(100):
            billing_service.incr_usage_for_tenant(db, t.id)
        res = billing_service.incr_usage_for_tenant(db, t.id)
        assert res["quota"] == 100
        assert res["is_overage"] is True


class TestInvoiceFormula:
    """P0-3：账单基础费/超额费按套餐与配置计算。"""

    def test_basic_invoice_formula(self, db, fake_redis):
        t = _mk_tenant(db, "basic")
        for _ in range(2100):
            billing_service.incr_usage_for_tenant(db, t.id)
        inv = billing_service.generate_invoice(db, tenant_id=t.id, period="2026-08")
        assert float(inv["base_fee"]) == 999.0
        assert float(inv["overage_fee"]) == 50.0  # 100 * 0.5
        assert float(inv["base_fee"] + inv["overage_fee"]) == 1049.0

    def test_pro_invoice_formula(self, db, fake_redis):
        t = _mk_tenant(db, "pro")
        for _ in range(10500):
            billing_service.incr_usage_for_tenant(db, t.id)
        inv = billing_service.generate_invoice(db, tenant_id=t.id, period="2026-08")
        assert float(inv["base_fee"]) == 4999.0
        assert float(inv["overage_fee"]) == 250.0

    def test_trial_no_invoice_by_default(self, db, fake_redis, monkeypatch):
        monkeypatch.setattr(settings, "billing_charge_trial", False)
        t = _mk_tenant(db, "trial")
        for _ in range(150):
            billing_service.incr_usage_for_tenant(db, t.id)
        from app.models.billing_invoice import BillingInvoice
        from sqlalchemy import select

        from app.tasks.billing_tasks import generate_monthly_invoices
        generate_monthly_invoices()
        rows = db.execute(select(BillingInvoice).where(BillingInvoice.tenant_id == t.id)).scalars().all()
        assert rows == []

    def test_trial_invoice_when_configured(self, db, fake_redis, monkeypatch):
        monkeypatch.setattr(settings, "billing_charge_trial", True)
        t = _mk_tenant(db, "trial")
        for _ in range(150):
            billing_service.incr_usage_for_tenant(db, t.id)
        inv = billing_service.generate_invoice(db, tenant_id=t.id, period="2026-08")
        assert float(inv["base_fee"]) == 0.0
        assert float(inv["overage_fee"]) == 25.0  # 50 * 0.5

    def test_no_overage_fee_within_quota(self, db, fake_redis):
        t = _mk_tenant(db, "basic")
        for _ in range(1000):
            billing_service.incr_usage_for_tenant(db, t.id)
        inv = billing_service.generate_invoice(db, tenant_id=t.id, period="2026-08")
        assert float(inv["base_fee"]) == 999.0
        assert float(inv["overage_fee"]) == 0.0
