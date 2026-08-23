"""
backend/tests/unit/test_order_state_machine.py

职责: 测试订单状态机合法跃迁 + 非法跃迁拦截 + 退款金额校验

测试内容:
  - TestValidTransitions: created->paid/cancelled, paid->shipped/refunding,
    shipped->completed, refunding->refunded；终态（completed/refunded/cancelled）无跃迁
  - TestInvalidTransitions: 非法跃迁抛 InvalidTransition + refunded 不可再退款
  - TestRefundValidation: 退款超额抛 RefundExceedsPaid
"""

import pytest

from app.models.order import VALID_TRANSITIONS


class TestValidTransitions:
    def test_created_to_paid(self):
        assert "paid" in VALID_TRANSITIONS["created"]

    def test_created_to_cancelled(self):
        assert "cancelled" in VALID_TRANSITIONS["created"]

    def test_paid_to_shipped(self):
        assert "shipped" in VALID_TRANSITIONS["paid"]

    def test_paid_to_refund_requested(self):
        assert "refund_requested" in VALID_TRANSITIONS["paid"]

    def test_shipped_to_completed(self):
        assert "completed" in VALID_TRANSITIONS["shipped"]

    def test_refunding_to_refunded(self):
        assert "refunded" in VALID_TRANSITIONS["refunding"]

    def test_completed_no_transitions(self):
        assert VALID_TRANSITIONS["completed"] == set()

    def test_refunded_no_transitions(self):
        assert VALID_TRANSITIONS["refunded"] == set()

    def test_cancelled_no_transitions(self):
        assert VALID_TRANSITIONS["cancelled"] == set()


class TestInvalidTransitions:
    def test_check_transition_raises(self):
        from app.services.order_service import _check_transition, InvalidTransition

        with pytest.raises(InvalidTransition, match="非法状态跃迁"):
            _check_transition("completed", "paid")

    def test_refunded_cannot_refund_again(self):
        from app.services.order_service import _check_transition, InvalidTransition

        with pytest.raises(InvalidTransition):
            _check_transition("refunded", "refunding")


class TestRefundValidation:
    """退款金额校验：调用 order_service.request_refund 真实验证金额边界."""

    def _make_paid_order(self, db, *, paid_amount=200.0, order_no="ORD-TEST001"):
        """创建一个 paid 状态订单供退款校验."""
        from app.models.order import Order

        order = Order(
            tenant_id=1, order_no=order_no, amount=paid_amount,
            paid_amount=paid_amount, status="paid",
        )
        db.add(order)
        db.flush()
        return order

    def test_amount_exceeds_paid(self, db):
        """退款超额应被 service 层拦截，抛 RefundExceedsPaid."""
        from app.services.order_service import request_refund, RefundExceedsPaid

        self._make_paid_order(db, paid_amount=200.0)
        db.commit()

        with pytest.raises(RefundExceedsPaid, match="超过订单实付金额"):
            request_refund(
                db, tenant_id=1, order_no="ORD-TEST001",
                amount=300.0, reason="test", client_request_id="cid-exceeds",
            )

    def test_amount_zero_rejected(self, db):
        """退款金额<=0 应被 service 层拒绝，抛 OrderError."""
        from app.services.order_service import request_refund, OrderError

        self._make_paid_order(db, paid_amount=200.0)
        db.commit()

        with pytest.raises(OrderError, match="退款金额必须大于 0"):
            request_refund(
                db, tenant_id=1, order_no="ORD-TEST001",
                amount=0, reason="test", client_request_id="cid-zero",
            )
