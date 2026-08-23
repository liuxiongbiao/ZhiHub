"""
backend/tests/contracts/test_cleanup_timeout.py

职责: 测试订单超时自动流转（PRD 3.6）与试用版30天降级只读（PRD 3.1）的契约

测试内容:
  - TestOrderTimeoutCleanup:
      * created 订单 updated_at 超 30min -> cancelled（待支付超时取消）
      * shipped 订单 updated_at 超 7 天 -> completed（发货自动完成）
      * created 订单 updated_at 10min 前 -> 不变（未超时）
  - TestTrialDegradation:
      * trial 租户 created_at 超 30 天 -> status=readonly
      * trial 租户 created_at 10 天前 -> 不变
      * basic 租户 created_at 超 30 天 -> 不变（非试用不降级）

防竞态: Core UPDATE WHERE updated_at < threshold 原子更新，与 cleanup_idle_sessions 同模式。
降级不中断: 只阻断新会话（status=readonly），进行中会话由 cleanup_idle_sessions 走完。
"""

import datetime

from sqlalchemy import text


# ═══════════════════════════════════════════════════════════
# PRD §3.6: 订单超时自动流转
# created 超 30min → cancelled；shipped 超 7 天 → completed
# ═══════════════════════════════════════════════════════════


def _make_order(db, status="created"):
    """插入一条订单并返回。"""
    from app.models.order import Order

    o = Order(
        tenant_id=1,
        order_no=f"NO-{status}-{id(db)}",
        amount=100.0,
        paid_amount=0.0,
        status=status,
        version=1,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def _set_order_updated_at(db, order_id, *, minutes_ago=None, days_ago=None):
    """raw SQL 把订单 updated_at 设为指定时间前（绕过 ORM onupdate）。"""
    delta = datetime.timedelta()
    if minutes_ago is not None:
        delta += datetime.timedelta(minutes=minutes_ago)
    if days_ago is not None:
        delta += datetime.timedelta(days=days_ago)
    old = datetime.datetime.utcnow() - delta
    db.execute(
        text("UPDATE orders SET updated_at=:old WHERE id=:id"),
        {"old": old, "id": order_id},
    )
    db.commit()


class TestOrderTimeoutCleanup:
    """cleanup_expired_orders 订单超时流转契约。"""

    def test_order_payment_timeout(self, db):
        """created 订单 updated_at 35min 前 -> cancelled。"""
        from app.tasks.cleanup_tasks import cleanup_expired_orders

        o = _make_order(db, status="created")
        _set_order_updated_at(db, o.id, minutes_ago=35)

        result = cleanup_expired_orders(db)
        db.refresh(o)
        assert o.status == "cancelled"
        assert result["orders_cancelled"] >= 1

    def test_order_shipped_timeout(self, db):
        """shipped 订单 updated_at 8 天前 -> completed。"""
        from app.tasks.cleanup_tasks import cleanup_expired_orders

        o = _make_order(db, status="shipped")
        _set_order_updated_at(db, o.id, days_ago=8)

        result = cleanup_expired_orders(db)
        db.refresh(o)
        assert o.status == "completed"
        assert result["orders_completed"] >= 1

    def test_order_not_timeout(self, db):
        """created 订单 updated_at 10min 前 -> 不变。"""
        from app.tasks.cleanup_tasks import cleanup_expired_orders

        o = _make_order(db, status="created")
        _set_order_updated_at(db, o.id, minutes_ago=10)

        cleanup_expired_orders(db)
        db.refresh(o)
        assert o.status == "created"


# ═══════════════════════════════════════════════════════════
# PRD §3.1: 试用版30天降级只读
# trial 且 created_at 超 30 天 且 status=active → readonly
# 只阻断新会话；进行中会话由 cleanup_idle_sessions 走完（降级不中断）
# ═══════════════════════════════════════════════════════════


def _make_tenant(db, plan_type="trial", status="active"):
    """插入一个租户并返回。"""
    from app.models.tenant import Tenant

    t = Tenant(
        name=f"T-{plan_type}-{status}-{id(db)}",
        plan_type=plan_type,
        status=status,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _set_tenant_created_at(db, tenant_id, days_ago):
    """raw SQL 把租户 created_at 设为 N 天前。"""
    old = datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)
    db.execute(
        text("UPDATE tenants SET created_at=:old WHERE id=:id"),
        {"old": old, "id": tenant_id},
    )
    db.commit()


class TestTrialDegradation:
    """cleanup_trial_degradation 试用版降级契约。"""

    def test_trial_degradation(self, db):
        """trial 租户 created_at 31 天前 -> readonly。"""
        from app.tasks.cleanup_tasks import cleanup_trial_degradation

        t = _make_tenant(db, plan_type="trial", status="active")
        _set_tenant_created_at(db, t.id, 31)

        result = cleanup_trial_degradation(db)
        db.refresh(t)
        assert t.status == "readonly"
        assert result["tenants_degraded"] >= 1

    def test_trial_not_expired(self, db):
        """trial 租户 created_at 10 天前 -> 不变。"""
        from app.tasks.cleanup_tasks import cleanup_trial_degradation

        t = _make_tenant(db, plan_type="trial", status="active")
        _set_tenant_created_at(db, t.id, 10)

        cleanup_trial_degradation(db)
        db.refresh(t)
        assert t.status == "active"

    def test_non_trial_not_degraded(self, db):
        """basic 租户 created_at 31 天前 -> 不变。"""
        from app.tasks.cleanup_tasks import cleanup_trial_degradation

        t = _make_tenant(db, plan_type="basic", status="active")
        _set_tenant_created_at(db, t.id, 31)

        cleanup_trial_degradation(db)
        db.refresh(t)
        assert t.status == "active"
