"""
backend/tests/unit/test_mcp_permission.py

职责: 测试 MCP 权限矩阵 + 入参校验（角色 x 工具 + 工单号/订单号/退款金额格式）
对应日程: 006-mcp-security

测试内容:
  - TestPermissionMatrix: admin 可退款（审批不暴露于 MCP），agent 不可退款但可查工单，user/未知角色全拒
  - TestParamValidation: 工单号/订单号格式校验 + 退款金额零/负数拒绝
"""

import pytest

from app.services.mcp_service import check_permission, validate_params, InvalidParams, PermissionDenied


class TestPermissionMatrix:
    def test_admin_can_request_refund(self):
        assert check_permission("admin", "request_refund") is True

    def test_agent_cannot_request_refund(self):
        assert check_permission("agent", "request_refund") is False

    def test_agent_can_get_ticket_status(self):
        assert check_permission("agent", "get_ticket_status") is True

    def test_user_no_permissions(self):
        assert check_permission("user", "get_ticket_status") is False
        assert check_permission("user", "query_order") is False

    def test_unknown_role(self):
        assert check_permission("unknown", "get_ticket_status") is False

    def test_approve_refund_not_exposed_via_mcp(self):
        """遗留2: 审批是坐席后台操作（HTTP 层 + approver_id 审计），MCP 调用链无审批人身份，
        不暴露给 LLM——admin 也应拒绝（原断言 True 与 遗留2 移除冲突，遗留2 未同步更新本测试）。"""
        assert check_permission("admin", "approve_refund") is False


class TestParamValidation:
    def test_valid_ticket_no(self):
        validate_params("get_ticket_status", {"ticket_no": "TK-12345678", "phone": "13800000001"})

    def test_invalid_ticket_no(self):
        with pytest.raises(InvalidParams, match="工单号格式错误"):
            validate_params("get_ticket_status", {"ticket_no": "INVALID", "phone": "13800000001"})

    def test_valid_order_no(self):
        validate_params("query_order", {"order_no": "ORD-12345678", "phone": "13800000001"})

    def test_invalid_order_no(self):
        with pytest.raises(InvalidParams, match="订单号格式错误"):
            validate_params("query_order", {"order_no": "BAD", "phone": "13800000001"})

    def test_refund_amount_zero(self):
        with pytest.raises(InvalidParams, match="退款金额"):
            validate_params("request_refund", {"order_no": "ORD-12345678", "amount": 0})

    def test_refund_amount_negative(self):
        with pytest.raises(InvalidParams, match="退款金额"):
            validate_params("request_refund", {"order_no": "ORD-12345678", "amount": -100})
