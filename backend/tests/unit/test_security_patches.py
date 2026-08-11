"""
backend/tests/unit/test_security_patches.py

职责: 测试安全补丁（坐席越权拦截 + 退款金额二次确认 + 幂等机制）
对应日程: 006-mcp-security

测试内容:
  - TestAgentPermissionBoundary: agent 角色访问 admin 端点 403 / admin 通过 / 租户 closed 拦截
  - TestRefundAmountDoubleCheck: 退款金额零/负数/超额拒绝 / 合法参数通过
  - TestIdempotencyDemo: 幂等键唯一性 + check-then-record 流程 + 角色权限复验
"""

import pytest


class TestAgentPermissionBoundary:
    """坐席越权拦截 - Constitution II 的测试证明."""

    def test_agent_should_not_access_billing(self):
        """坐席无权访问账单API（role=agent不可调billing端点）."""
        from app.api.deps import require_role
        # require_role("admin") 闭包: agent 角色调用应被拦截
        checker = require_role("admin")
        # 创建一个mock request
        class MockRequest:
            class state:
                role = "agent"
        req = MockRequest()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            checker(req)
        assert exc.value.status_code == 403

    def test_admin_can_access(self):
        """管理员可以访问."""
        from app.api.deps import require_role
        checker = require_role("admin")
        class MockRequest:
            class state:
                role = "admin"
        req = MockRequest()
        # 不应抛异常
        checker(req)

    def test_require_tenant_closed(self, db):
        """租户已注销 -> require_tenant 闭包抛 403."""
        from app.api.deps import require_tenant
        from app.crud import tenant as tenant_crud
        from fastapi import HTTPException

        # 建一个 closed 状态的租户
        tenant = tenant_crud.create_tenant(db, name="已注销公司", status="closed")
        db.commit()

        checker = require_tenant()

        class MockRequest:
            class state:
                tenant_id = tenant.id

        with pytest.raises(HTTPException) as exc:
            checker(MockRequest(), db=db)
        assert exc.value.status_code == 403
        assert "注销" in exc.value.detail


class TestRefundAmountDoubleCheck:
    """退款金额上限二次确认."""

    def test_amount_zero_blocked(self):
        """退款金额=0 -> 拒绝."""
        from app.services.mcp_service import validate_params, InvalidParams
        with pytest.raises(InvalidParams, match="退款金额"):
            validate_params("request_refund", {"order_no": "ORD-12345678", "amount": 0})

    def test_amount_negative_blocked(self):
        """退款金额<0 -> 拒绝."""
        from app.services.mcp_service import validate_params, InvalidParams
        with pytest.raises(InvalidParams, match="退款金额"):
            validate_params("request_refund", {"order_no": "ORD-12345678", "amount": -50})

    def test_amount_exceeds_paid_blocked(self, db):
        """退款超额 -> order_service 抛 RefundExceedsPaid."""
        from app.models.order import Order
        from app.services.order_service import request_refund, RefundExceedsPaid

        # 建一个 paid 订单，实付 300
        order = Order(
            tenant_id=1, order_no="ORD-SEC001", amount=300.0,
            paid_amount=300.0, status="paid",
        )
        db.add(order)
        db.flush()
        db.commit()

        with pytest.raises(RefundExceedsPaid, match="超过订单实付金额"):
            request_refund(
                db, tenant_id=1, order_no="ORD-SEC001",
                amount=500.0, reason="test", client_request_id="cid-sec-exceeds",
            )

    def test_valid_refund_params_pass(self):
        """合法退款参数应通过校验."""
        from app.services.mcp_service import validate_params
        validate_params("request_refund", {"order_no": "ORD-12345678", "amount": 100, "client_request_id": "REQ-001"})


class TestIdempotencyDemo:
    """幂等演示: 同client_request_id重复请求只创建一条记录."""

    def test_idempotency_key_generation(self):
        """幂等键唯一性 + 命名空间隔离."""
        from app.core.idempotency import _idempotency_key
        key1 = _idempotency_key(1, "REQ-001")
        key2 = _idempotency_key(2, "REQ-001")  # 跨订单（跨租户）同 id -> 不同键
        key3 = _idempotency_key(1, "REQ-002")
        assert key1 != key2
        assert key1 != key3
        assert _idempotency_key(1, "REQ-001") == key1  # 相同输入相同输出

    def test_check_then_record_flow(self):
        """幂等检查流程: 未记录->执行业务->记录->再次检查命中."""
        from app.core.idempotency import check_idempotency, record_idempotency

        cid = "TEST-IDEM-" + str(id({}))
        # 首次检查: 不存在
        assert check_idempotency(1, cid) is None
        # 记录
        record_idempotency(1, cid, "refund", 1)
        # 再次检查: 命中
        cached = check_idempotency(1, cid)
        assert cached is not None
        assert cached["resource_type"] == "refund"
        assert cached["resource_id"] == 1

    def test_cross_order_same_id_not_shared(self):
        """跨订单（跨租户）同 client_request_id 不共享幂等记录（T6 防碰撞）."""
        from app.core.idempotency import check_idempotency, record_idempotency

        cid = "TEST-XORD-" + str(id({}))
        record_idempotency(10, cid, "refund", 100)  # 订单 10 记录
        assert check_idempotency(10, cid) is not None  # 同订单命中
        assert check_idempotency(20, cid) is None  # 另一订单（可能属其他租户）不受影响

    def test_agent_cannot_call_refund_tool(self):
        """agent角色不可调用request_refund工具."""
        from app.services.mcp_service import check_permission
        assert check_permission("agent", "request_refund") is False

    def test_admin_can_call_refund_tool(self):
        """admin角色可以调用request_refund工具."""
        from app.services.mcp_service import check_permission
        assert check_permission("admin", "request_refund") is True
