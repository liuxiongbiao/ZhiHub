"""批次 2 回归测试：会话计费事件、每日聚合时序与调度成功标记。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


def _idle_billable_session(db, *, tenant_id: int = 1):
    from app.models.session import Session

    session = Session(
        tenant_id=tenant_id,
        status="agent_handling",
        effective_reply=True,
        billed=False,
    )
    db.add(session)
    db.commit()
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=31)
    db.execute(
        text("UPDATE sessions SET updated_at=:old WHERE id=:id"),
        {"old": old, "id": session.id},
    )
    db.commit()
    return session


class _FailFirstPipelineRedis:
    """仅让第一次 Redis 事务提交失败，其余命令委托给 fakeredis。"""

    def __init__(self, inner):
        self.inner = inner
        self.failed = False

    def pipeline(self, *args, **kwargs):
        pipeline = self.inner.pipeline(*args, **kwargs)
        original_execute = pipeline.execute

        def execute(*execute_args, **execute_kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("redis unavailable")
            return original_execute(*execute_args, **execute_kwargs)

        pipeline.execute = execute
        return pipeline

    def __getattr__(self, name):
        return getattr(self.inner, name)


class TestSessionBillingEvent:
    def test_redis_failure_retries_event_without_double_charge(self, db, fake_redis, monkeypatch):
        from app.models.billing_event import BillingEvent
        from app.services import billing_service
        from app.tasks.cleanup_tasks import cleanup_idle_sessions

        session = _idle_billable_session(db)
        flaky_redis = _FailFirstPipelineRedis(fake_redis)
        monkeypatch.setattr(billing_service, "get_redis", lambda: flaky_redis)

        cleanup_idle_sessions(db)
        db.refresh(session)
        event = db.query(BillingEvent).filter_by(session_id=session.id).one()
        assert session.billed is True
        assert event.status == "pending"
        assert float(fake_redis.get(billing_service._quota_key(session.tenant_id)) or 0) == 0

        cleanup_idle_sessions(db)
        db.refresh(event)
        assert event.status == "processed"
        assert float(fake_redis.get(billing_service._quota_key(session.tenant_id))) == 1.0

        cleanup_idle_sessions(db)
        assert db.query(BillingEvent).filter_by(session_id=session.id).count() == 1
        assert float(fake_redis.get(billing_service._quota_key(session.tenant_id))) == 1.0


class TestDailyAggregationOrdering:
    def test_db_commit_failure_keeps_snapshot_and_record_retryable(self, db, fake_redis, monkeypatch):
        from app.models.usage_record import UsageRecord
        from app.tasks import billing_tasks

        monkeypatch.setattr(billing_tasks, "SessionLocal", lambda: db)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        quota_key = f"billing:quota:9:{month}"
        snapshot_key = f"billing:usage_last:9:{month}"
        fake_redis.set(quota_key, "12.5")

        original_commit = db.commit
        monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        with pytest.raises(RuntimeError, match="db down"):
            billing_tasks.aggregate_daily_usage()

        assert fake_redis.get(snapshot_key) is None
        assert db.query(UsageRecord).count() == 0

        monkeypatch.setattr(db, "commit", original_commit)
        result = billing_tasks.aggregate_daily_usage()
        assert result["status"] == "ok"
        assert db.query(UsageRecord).filter_by(tenant_id=9).count() == 1
        assert float(fake_redis.get(snapshot_key)) == 12.5

    def test_snapshot_failure_replays_committed_record_without_duplicate(self, db, fake_redis, monkeypatch):
        from app.models.usage_record import UsageRecord
        from app.tasks import billing_tasks

        monkeypatch.setattr(billing_tasks, "SessionLocal", lambda: db)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        quota_key = f"billing:quota:10:{month}"
        snapshot_key = f"billing:usage_last:10:{month}"
        fake_redis.set(quota_key, "7")

        original_set = fake_redis.set

        def fail_snapshot(key, *args, **kwargs):
            if key == snapshot_key:
                raise RuntimeError("snapshot down")
            return original_set(key, *args, **kwargs)

        monkeypatch.setattr(fake_redis, "set", fail_snapshot)
        with pytest.raises(RuntimeError, match="snapshot down"):
            billing_tasks.aggregate_daily_usage()
        assert db.query(UsageRecord).filter_by(tenant_id=10).count() == 1

        monkeypatch.setattr(fake_redis, "set", original_set)
        billing_tasks.aggregate_daily_usage()
        row = db.query(UsageRecord).filter_by(tenant_id=10).one()
        assert row.conversation_count == 7
        assert float(fake_redis.get(snapshot_key)) == 7.0

    def test_invoice_commit_failure_rerun_keeps_single_period_invoice(self, db, monkeypatch):
        from app.models.billing_invoice import BillingInvoice
        from app.models.tenant import Tenant
        from app.tasks import billing_tasks

        tenant = Tenant(name="batch2-basic", plan_type="basic", status="active")
        db.add(tenant)
        db.commit()
        tenant_id = tenant.id
        monkeypatch.setattr(billing_tasks, "SessionLocal", lambda: db)

        original_commit = db.commit
        monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("invoice db down")))
        with pytest.raises(RuntimeError, match="invoice db down"):
            billing_tasks.generate_monthly_invoices()
        assert db.query(BillingInvoice).filter_by(tenant_id=tenant_id).count() == 0

        monkeypatch.setattr(db, "commit", original_commit)
        billing_tasks.generate_monthly_invoices()
        billing_tasks.generate_monthly_invoices()
        assert db.query(BillingInvoice).filter_by(tenant_id=tenant_id).count() == 1


class TestBillingSchedulerMarker:
    def test_ambiguous_task_result_does_not_advance_marker(self):
        from app.main import _billing_scheduler_tick

        state = {"last_usage_day": None, "last_invoice_month": "2026-08"}
        with pytest.raises(RuntimeError, match="did not confirm success"):
            _billing_scheduler_tick(
                datetime(2026, 8, 16, 2, 1),
                state,
                aggregate_task=lambda: None,
            )
        assert state["last_usage_day"] is None

    def test_failed_daily_task_does_not_advance_marker_and_can_retry(self):
        from app.main import _billing_scheduler_tick

        now = datetime(2026, 8, 16, 2, 1)
        state = {"last_usage_day": None, "last_invoice_month": "2026-08"}

        with pytest.raises(RuntimeError, match="aggregate failed"):
            _billing_scheduler_tick(
                now,
                state,
                aggregate_task=lambda: {"status": "error", "error": "aggregate failed"},
                invoice_task=lambda: {"status": "ok"},
            )
        assert state["last_usage_day"] is None

        _billing_scheduler_tick(
            now,
            state,
            aggregate_task=lambda: {"status": "ok", "tenants": 1},
            invoice_task=lambda: {"status": "ok"},
        )
        assert state["last_usage_day"] == "2026-08-16"
