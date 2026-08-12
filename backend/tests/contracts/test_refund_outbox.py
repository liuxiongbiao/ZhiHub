"""契约测试：P1-1 审批只追加 + P1-2 退款 Outbox。"""

import json

import pytest

from app.models.order import Order
from app.models.refund_approval import RefundApproval
from app.models.refund_outbox import RefundOutbox


def _make_refund(db, *, amount=100.0, status="pending"):
    order = Order(tenant_id=1, order_no="ORD-OBX-1", amount=299.0, paid_amount=299.0, status="refunding")
    db.add(order)
    db.flush()
    approval = RefundApproval(
        order_id=order.id, amount=amount, reason="test",
        client_request_id=f"cid-obx-{order.id}", status=status,
    )
    db.add(approval)
    db.commit()
    db.refresh(order)
    db.refresh(approval)
    return order, approval


class TestReviewHistoryAppendOnly:
    def test_large_refund_chain_preserves_both_approvers(self, db):
        from app.services.order_service import approve_refund
        _, approval = _make_refund(db, amount=1500.0)
        r1 = approve_refund(db, approval_id=approval.id, decision="approved",
                             approver_id=10, tenant_id=1, role="agent")
        assert r1["status"] == "pending"
        r2 = approve_refund(db, approval_id=approval.id, decision="approved",
                             approver_id=1, tenant_id=1, role="admin")
        assert r2["status"] == "approved"

        db.refresh(approval)
        history = json.loads(approval.review_history)
        assert [h["action"] for h in history] == ["pre_approve", "approved"]
        assert history[0]["approver_id"] == 10  # 初审人保留
        assert approval.approver_id == 1        # 终审人

    def test_cancel_appends_review(self, db):
        from app.services.order_service import cancel_refund
        _, approval = _make_refund(db)
        cancel_refund(db, approval_id=approval.id, tenant_id=1)
        db.refresh(approval)
        history = json.loads(approval.review_history)
        assert history[-1]["action"] == "cancel"


class TestRefundOutbox:
    def test_approve_success_marks_success_and_refunded(self, db):
        from app.services.order_service import approve_refund
        order, approval = _make_refund(db)
        result = approve_refund(db, approval_id=approval.id, decision="approved",
                                 approver_id=1, tenant_id=1, role="admin")
        assert result["status"] == "approved"
        db.expire_all()
        assert db.get(Order, order.id).status == "refunded"
        outbox = db.query(RefundOutbox).filter_by(approval_id=approval.id).one()
        assert outbox.status == "success"

    def test_failure_keeps_outbox_pending_and_retryable(self, db, monkeypatch):
        from app.services import order_service as osvc
        order, approval = _make_refund(db)

        monkeypatch.setattr(osvc, "_invoke_alipay_refund", lambda db_, order_, amount, trade_no=None: False)
        with pytest.raises(osvc.RefundError):
            osvc.approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=1, tenant_id=1, role="admin")
        db.expire_all()
        assert db.get(Order, order.id).status == "refunding"
        assert db.get(RefundApproval, approval.id).status == "approved"
        outbox = db.query(RefundOutbox).filter_by(approval_id=approval.id).one()
        assert outbox.status == "pending"

        # 重试成功
        monkeypatch.setattr(osvc, "_invoke_alipay_refund", lambda db_, order_, amount, trade_no=None: True)
        assert osvc.process_refund_outbox(db, approval.id) is True
        db.expire_all()
        assert db.get(Order, order.id).status == "refunded"
        assert db.query(RefundOutbox).filter_by(approval_id=approval.id).one().status == "success"

    def test_money_moved_cas_conflict_marks_manual_review(self, db, monkeypatch):
        from app.services import order_service as osvc
        order, approval = _make_refund(db)

        invoked = []
        monkeypatch.setattr(
            osvc, "_invoke_alipay_refund",
            lambda db_, order_, amount, trade_no=None: invoked.append(1) or True,
        )
        monkeypatch.setattr(
            osvc.order_crud, "update_status_optimistic",
            lambda db_, order_, s: False,
        )
        with pytest.raises(osvc.RefundError):
            osvc.approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=1, tenant_id=1, role="admin")
        assert len(invoked) == 1  # 资金只动一次
        db.expire_all()
        outbox = db.query(RefundOutbox).filter_by(approval_id=approval.id).one()
        assert outbox.status == "success"  # 防止重复退款
        assert "manual review" in (outbox.last_error or "")

    def test_batch_retries_pending(self, db, monkeypatch):
        from app.services import order_service as osvc
        _, approval = _make_refund(db)
        from app.models.refund_outbox import RefundOutbox
        db.add(RefundOutbox(
            approval_id=approval.id, order_id=approval.order_id,
            amount=approval.amount, trade_no="t1", status="pending",
        ))
        db.commit()
        monkeypatch.setattr(osvc, "_invoke_alipay_refund", lambda db_, order_, amount, trade_no=None: True)
        res = osvc.process_refund_outbox_batch(db)
        assert res == {"success": 1, "failed": 0}
