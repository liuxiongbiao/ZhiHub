"""批次 1 回归测试：退款状态机、审批并发、Outbox 租约与不可变事件。"""

from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, StatementError
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.order import Order, VALID_TRANSITIONS
from app.models.refund_approval import RefundApproval
from app.models.refund_approval_event import RefundApprovalEvent
from app.models.refund_outbox import RefundOutbox


def _order(db, *, order_no: str, status: str = "paid", tenant_id: int = 1) -> Order:
    order = Order(
        tenant_id=tenant_id,
        order_no=order_no,
        amount=299,
        paid_amount=299,
        status=status,
        payment_trade_no=f"PAY-{order_no}",
    )
    db.add(order)
    db.commit()
    return order


class TestRefundRequestedStateMachine:
    def test_paid_request_approve_channel_confirm_full_path(self, db, monkeypatch):
        from app.services import order_service

        order = _order(db, order_no="ORD-B1-STATE")
        requested = order_service.request_refund(
            db,
            tenant_id=1,
            order_no=order.order_no,
            amount=99,
            reason="测试",
            client_request_id="b1-state",
        )
        db.refresh(order)
        assert order.status == "refund_requested"
        assert "refunding" not in VALID_TRANSITIONS["paid"]
        assert VALID_TRANSITIONS["refund_requested"] == {"refunding", "paid", "shipped"}

        process_outbox = order_service.process_refund_outbox
        monkeypatch.setattr(order_service, "process_refund_outbox", lambda *_a, **_k: None)
        order_service.approve_refund(
            db,
            approval_id=requested["id"],
            decision="approved",
            approver_id=1,
            tenant_id=1,
            role="admin",
        )
        db.refresh(order)
        assert order.status == "refunding"

        monkeypatch.setattr(order_service, "process_refund_outbox", process_outbox)
        monkeypatch.setattr(order_service, "_query_alipay_refund", lambda *_a, **_k: False)
        monkeypatch.setattr(order_service, "_invoke_alipay_refund", lambda *_a, **_k: True)
        assert order_service.process_refund_outbox(db, requested["id"]) is True
        db.refresh(order)
        assert order.status == "refunded"

    @pytest.mark.parametrize("original", ["paid", "shipped"])
    def test_reject_restores_exact_pre_request_status(self, db, original):
        from app.services.order_service import approve_refund, request_refund

        order = _order(db, order_no=f"ORD-B1-REJECT-{original}", status=original)
        requested = request_refund(
            db,
            tenant_id=1,
            order_no=order.order_no,
            amount=20,
            reason=None,
            client_request_id=f"b1-reject-{original}",
        )
        approve_refund(
            db,
            approval_id=requested["id"],
            decision="rejected",
            approver_id=1,
            tenant_id=1,
            role="admin",
        )
        db.refresh(order)
        assert order.status == original

    def test_cancel_restores_exact_pre_request_status(self, db):
        from app.services.order_service import cancel_refund, request_refund

        order = _order(db, order_no="ORD-B1-CANCEL", status="shipped")
        requested = request_refund(
            db,
            tenant_id=1,
            order_no=order.order_no,
            amount=20,
            reason=None,
            client_request_id="b1-cancel",
        )
        cancel_refund(db, approval_id=requested["id"], tenant_id=1, reason="不需要了")
        db.refresh(order)
        assert order.status == "shipped"


class TestRefundApprovalConcurrency:
    def test_two_approval_requests_create_only_one_outbox(self, db, monkeypatch):
        """模拟并发赢家提交后，使用旧页面发出的第二请求只能命中条件更新失败分支。"""
        from app.services import order_service

        order = _order(db, order_no="ORD-B1-RACE")
        requested = order_service.request_refund(
            db,
            tenant_id=1,
            order_no=order.order_no,
            amount=20,
            reason=None,
            client_request_id="b1-race",
        )
        monkeypatch.setattr(order_service, "process_refund_outbox", lambda *_a, **_k: None)

        first = order_service.approve_refund(
            db,
            approval_id=requested["id"],
            decision="approved",
            approver_id=1,
            tenant_id=1,
            role="admin",
        )
        second = order_service.approve_refund(
            db,
            approval_id=requested["id"],
            decision="approved",
            approver_id=2,
            tenant_id=1,
            role="admin",
        )

        assert first["status"] == "approved"
        assert second["already_processed"] is True
        assert db.query(RefundOutbox).filter_by(approval_id=requested["id"]).count() == 1

    def test_two_database_sessions_only_one_claims_and_creates_outbox(self, tmp_path):
        """两个数据库会话同时持有 pending 快照时，条件更新只允许一个成为赢家。"""
        from app.crud import order as order_crud

        engine = create_engine(
            f"sqlite:///{tmp_path / 'refund-race.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
        with SessionLocal() as seed:
            order = Order(
                tenant_id=1,
                order_no="ORD-B1-DB-RACE",
                amount=100,
                paid_amount=100,
                status="refund_requested",
                payment_trade_no="PAY-B1-DB-RACE",
            )
            seed.add(order)
            seed.flush()
            approval = RefundApproval(
                order_id=order.id,
                amount=20,
                client_request_id="b1-db-race",
                status="pending",
                pre_status="paid",
            )
            seed.add(approval)
            seed.commit()
            approval_id = approval.id
            order_id = order.id

        barrier = Barrier(2)
        results: list[bool] = []

        def compete(approver_id: int) -> None:
            with SessionLocal() as session:
                session.get(RefundApproval, approval_id)
                barrier.wait()
                won = order_crud.claim_refund_decision(
                    session,
                    approval_id=approval_id,
                    decision="approved",
                    approver_id=approver_id,
                )
                if won:
                    session.add(RefundOutbox(
                        approval_id=approval_id,
                        order_id=order_id,
                        amount=20,
                        trade_no="PAY-B1-DB-RACE",
                    ))
                    session.commit()
                else:
                    session.rollback()
                results.append(won)

        workers = [Thread(target=compete, args=(value,)) for value in (11, 12)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)

        assert not any(worker.is_alive() for worker in workers)
        assert sorted(results) == [False, True]
        with SessionLocal() as verify:
            assert verify.query(RefundOutbox).filter_by(approval_id=approval_id).count() == 1
        engine.dispose()


class TestRefundOutboxLease:
    def test_stale_processing_is_reconciled_then_retried_with_stable_request_no(self, db, monkeypatch):
        from app.services import order_service

        order = _order(db, order_no="ORD-B1-LEASE", status="refunding")
        approval = RefundApproval(
            order_id=order.id,
            amount=50,
            client_request_id="b1-lease",
            status="approved",
            pre_status="paid",
        )
        db.add(approval)
        db.flush()
        outbox = RefundOutbox(
            approval_id=approval.id,
            order_id=order.id,
            amount=50,
            trade_no=order.payment_trade_no,
            status="processing",
            processing_at=datetime.utcnow() - timedelta(minutes=10),
            attempts=1,
        )
        db.add(outbox)
        db.commit()

        calls: list[tuple[str, str]] = []
        monkeypatch.setattr(
            order_service,
            "_query_alipay_refund",
            lambda _trade, request_no: calls.append(("query", request_no)) or False,
        )
        monkeypatch.setattr(
            order_service,
            "_invoke_alipay_refund",
            lambda _db, _order, _amount, _trade, request_no: calls.append(("refund", request_no)) or True,
        )

        result = order_service.process_refund_outbox_batch(db, lease_seconds=60)
        expected_request_no = order_service.refund_request_no(approval.id)
        assert result == {"success": 1, "failed": 0}
        assert calls == [("query", expected_request_no), ("refund", expected_request_no)]
        db.refresh(outbox)
        assert outbox.status == "success"
        assert outbox.processing_at is None

    def test_recovery_lands_confirmed_channel_refund_without_resending(self, db, monkeypatch):
        from app.services import order_service

        order = _order(db, order_no="ORD-B1-RECON", status="refunding")
        approval = RefundApproval(
            order_id=order.id,
            amount=50,
            client_request_id="b1-recon",
            status="approved",
            pre_status="paid",
        )
        db.add(approval)
        db.flush()
        outbox = RefundOutbox(
            approval_id=approval.id,
            order_id=order.id,
            amount=50,
            trade_no=order.payment_trade_no,
            status="processing",
            processing_at=datetime.utcnow() - timedelta(minutes=10),
            attempts=1,
        )
        db.add(outbox)
        db.commit()

        monkeypatch.setattr(order_service, "_query_alipay_refund", lambda *_a: True)
        monkeypatch.setattr(
            order_service,
            "_invoke_alipay_refund",
            lambda *_a, **_k: pytest.fail("渠道已确认退款时不得重发"),
        )
        assert order_service.process_refund_outbox_batch(db, lease_seconds=60)["success"] == 1
        db.refresh(order)
        assert order.status == "refunded"


class TestRefundApprovalEvents:
    def test_migration_only_tolerates_mysql_trigger_privilege_error(self, monkeypatch):
        migration_path = Path(__file__).parents[2] / "alembic" / "versions" / "d30_refund_approval_safety.py"
        spec = importlib.util.spec_from_file_location("d30_refund_approval_safety", migration_path)
        migration = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(migration)

        denied = OperationalError("CREATE TRIGGER", {}, Exception(1419, "SUPER privilege required"))
        execute = Mock(side_effect=denied)
        monkeypatch.setattr(migration.op, "execute", execute)
        migration._create_mysql_immutability_triggers()
        assert execute.call_count == 1

        unexpected = OperationalError("CREATE TRIGGER", {}, Exception(1142, "TRIGGER denied"))
        monkeypatch.setattr(migration.op, "execute", Mock(side_effect=unexpected))
        with pytest.raises(OperationalError):
            migration._create_mysql_immutability_triggers()

    def test_new_refund_writes_requested_and_final_events(self, db):
        from app.services.order_service import approve_refund, request_refund

        order = _order(db, order_no="ORD-B1-EVENT")
        requested = request_refund(
            db,
            tenant_id=1,
            order_no=order.order_no,
            amount=20,
            reason=None,
            client_request_id="b1-event",
        )
        approve_refund(
            db,
            approval_id=requested["id"],
            decision="rejected",
            approver_id=7,
            tenant_id=1,
            role="admin",
        )
        events = db.query(RefundApprovalEvent).filter_by(approval_id=requested["id"]).order_by(RefundApprovalEvent.id).all()
        assert [event.event_type for event in events] == ["requested", "rejected"]

    def test_event_rows_reject_update_and_delete(self, db):
        event = RefundApprovalEvent(approval_id=1, event_type="requested", actor_role="user")
        db.add(event)
        db.commit()

        event.event_type = "approved"
        with pytest.raises((ValueError, StatementError), match="不可变"):
            db.commit()
        db.rollback()

        persisted = db.get(RefundApprovalEvent, event.id)
        db.delete(persisted)
        with pytest.raises((ValueError, StatementError), match="不可变"):
            db.commit()


class TestAgentRefundApprovalApi:
    def test_agent_can_list_rules_and_finalize_small_refund(self, client, db, make_token):
        from app.services.order_service import request_refund

        order = _order(db, order_no="ORD-B1-AGENT")
        requested = request_refund(
            db,
            tenant_id=1,
            order_no=order.order_no,
            amount=20,
            reason=None,
            client_request_id="b1-agent",
        )
        headers = {"Authorization": f"Bearer {make_token(user_id=3, tenant_id=1, role='agent')}"}

        rules = client.get("/api/v1/order/refunds/rules", headers=headers)
        assert rules.status_code == 200
        assert rules.json()["approval_threshold"] > 0

        response = client.post(
            f"/api/v1/order/refunds/{requested['id']}/approve",
            headers=headers,
            json={"decision": "approved"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"
