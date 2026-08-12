"""
backend/tests/contracts/test_billing_tasks.py

职责: 资金逻辑回归防护（T1/T9 修复，此前零测试）

测试内容:
  - TestAggregateDailyUsage: 聚合任务崩溃修复（浮点计数）+ 当日增量 + is_overage
  - TestGenerateInvoiceByPeriod: 账单按账期 period 读配额 key（月初账单超额费正确）

背景: 这组测试曾零覆盖——T1 的 int("12.5") 崩溃、T9 的月初账单超额费恒 0
      都因无测试而漏过，现补回归防护。
"""
import pytest
from datetime import datetime, timedelta, timezone


class TestAggregateDailyUsage:
    """T1: 聚合任务 — 浮点不崩溃 / 幂等 / 当日增量 / is_overage。"""

    def test_float_count_no_crash_and_round(self, db, monkeypatch, fake_redis):
        """INCRBYFLOAT 计数（"12.5"）不崩溃且正确取整（13）。"""
        import app.tasks.billing_tasks as bt
        from app.models.usage_record import UsageRecord

        monkeypatch.setattr(bt, "SessionLocal", lambda: db)
        fake_redis.set("billing:quota:1:2026-08", "12.5")
        result = bt.aggregate_daily_usage()
        assert result["status"] == "ok" and result["tenants"] == 1, result
        rec = db.query(UsageRecord).one()
        assert rec.conversation_count == 13
        assert rec.is_overage is False

    def test_idempotent_same_day(self, db, monkeypatch, fake_redis):
        """同一天重复执行 -> 不产生重复记录。"""
        import app.tasks.billing_tasks as bt
        from app.models.usage_record import UsageRecord

        monkeypatch.setattr(bt, "SessionLocal", lambda: db)
        fake_redis.set("billing:quota:1:2026-08", "12.5")
        bt.aggregate_daily_usage()
        bt.aggregate_daily_usage()
        assert db.query(UsageRecord).count() == 1

    def test_daily_increment_uses_snapshot(self, db, monkeypatch, fake_redis):
        """跨日聚合: 增量 = 当前累计 - 上次快照（当日对话数语义）。"""
        import app.tasks.billing_tasks as bt
        from app.models.usage_record import UsageRecord

        monkeypatch.setattr(bt, "SessionLocal", lambda: db)
        base = datetime(2026, 8, 10, 2, 0, 0, tzinfo=timezone.utc)

        class _Day1:
            @staticmethod
            def now(tz=None):
                return base
        monkeypatch.setattr(bt, "datetime", _Day1)
        fake_redis.set("billing:quota:1:2026-08", "15.5")
        bt.aggregate_daily_usage()  # 第一天: 累计 15.5 -> 16

        class _Day2:
            @staticmethod
            def now(tz=None):
                return base + timedelta(days=1)
        monkeypatch.setattr(bt, "datetime", _Day2)
        fake_redis.set("billing:quota:1:2026-08", "18.5")
        bt.aggregate_daily_usage()  # 第二天: 18.5-15.5=3.0 -> 3

        recs = db.query(UsageRecord).order_by(UsageRecord.date).all()
        assert [r.conversation_count for r in recs] == [16, 3]

    def test_is_overage_flag(self, db, monkeypatch, fake_redis):
        """超配额租户聚合时 is_overage=True。"""
        import app.tasks.billing_tasks as bt
        from app.models.usage_record import UsageRecord

        monkeypatch.setattr(bt, "SessionLocal", lambda: db)
        fake_redis.set("billing:quota:2:2026-08", "150")
        bt.aggregate_daily_usage()
        rec = db.query(UsageRecord).one()
        assert rec.conversation_count == 150
        assert rec.is_overage is True

    def test_cross_month_keys_do_not_pollute(self, db, monkeypatch, fake_redis):
        """H1 回归: 跨月 quota key 共存时只处理当月 key，上月累计不污染当日增量。

        上月 key 因 40 天 TTL 仍存在；若不按月份过滤，scan 会用上月全月累计(150)
        对比当月快照(0) -> day_count=150 写入今日，当月真增量(30)因幂等命中丢失。
        """
        import app.tasks.billing_tasks as bt
        from app.models.usage_record import UsageRecord
        from datetime import datetime, timezone

        monkeypatch.setattr(bt, "SessionLocal", lambda: db)
        now = datetime.now(timezone.utc)
        cur_month = now.strftime("%Y-%m")
        # 上月: 当月1号回退一天
        last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        fake_redis.set(f"billing:quota:7:{last_month}", "150")  # 上月残留 key
        fake_redis.set(f"billing:quota:7:{cur_month}", "30")    # 当月真实计数

        result = bt.aggregate_daily_usage()
        assert result["status"] == "ok"
        rec = db.query(UsageRecord).one()
        # 当日增量应为当月累计 30，不是上月总量 150
        assert rec.conversation_count == 30, f"跨月污染: 期望 30 实得 {rec.conversation_count}"


class TestGenerateInvoiceByPeriod:
    """T9: 账单按账期 period 读配额 key（月初生成上月账单）。"""

    def test_previous_month_invoice_reads_previous_quota(self, db, fake_redis):
        """月初生成上月账单: 读上月 key，超额费正确；当前月计数不串扰。"""
        from app.services.billing_service import generate_invoice
        from app.models.tenant import Tenant

        db.add(Tenant(name="basic-tenant", plan_type="basic", status="active"))
        db.commit()

        fake_redis.set("billing:quota:1:2026-07", "2500")  # basic 配额 2000，超额 500
        fake_redis.set("billing:quota:1:2026-08", "30")  # 当前月计数，不应影响 7 月账单
        result = generate_invoice(db, tenant_id=1, period="2026-07")
        assert float(result["base_fee"]) == 999.0
        assert float(result["overage_fee"]) == 250.0  # 500 * 0.5

    def test_idempotent_same_period(self, db, fake_redis):
        """同账期重复生成 -> cached=True，不重复计费。"""
        from app.services.billing_service import generate_invoice
        from app.models.tenant import Tenant

        db.add(Tenant(name="basic-tenant", plan_type="basic", status="active"))
        db.commit()

        fake_redis.set("billing:quota:1:2026-07", "2500")
        r1 = generate_invoice(db, tenant_id=1, period="2026-07")
        r2 = generate_invoice(db, tenant_id=1, period="2026-07")
        assert r2["cached"] is True
        assert float(r2["overage_fee"]) == float(r1["overage_fee"]) == 250.0

    def test_under_quota_month_no_overage(self, db, fake_redis):
        """未超额账期 -> overage_fee=0。"""
        from app.services.billing_service import generate_invoice

        fake_redis.set("billing:quota:1:2026-06", "80")
        result = generate_invoice(db, tenant_id=1, period="2026-06")
        assert result["overage_fee"] == 0.0
