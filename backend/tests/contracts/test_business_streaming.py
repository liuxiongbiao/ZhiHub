"""
backend/tests/contracts/test_business_streaming.py

职责: 测试 010 业务闭环与流式输出的契约（退款撤销/分级/SSE/知识库质量/计费折算）

测试内容:
  - TestRefundCancel: 退款撤销契约（pending 可撤，approved 不可撤）
  - TestRefundGradedApproval: 退款分级审批（大额>1000 需 admin，agent 仅批小额）
  - TestSSEStreaming: SSE 流式格式（data: chunk + [DONE]）
  - TestKnowledgeQuality: 知识库质量检测（乱码率）+ 存储上限
  - TestBillingWeighting: 计费模型折算 + CSV 导出 + 逾期降级
"""

import pytest


def _make_refund(db, amount=100.0, status="pending"):
    """创建测试订单 + 退款审批。"""
    from app.models.order import Order
    from app.models.refund_approval import RefundApproval

    order = Order(tenant_id=1, order_no="ORD-T001", amount=299.0, paid_amount=299.0, status="refunding")
    db.add(order)
    db.flush()
    approval = RefundApproval(
        order_id=order.id, amount=amount, reason="test",
        client_request_id=f"cid-{order.id}", status=status,
    )
    db.add(approval)
    db.flush()
    return order, approval


# ═══════════════════════════════════════════════════════════
# T003: 退款撤销/分级审批（User Story 1）
# ═══════════════════════════════════════════════════════════
class TestRefundCancel:
    """退款撤销契约。"""

    def test_cancel_pending(self, db):
        from app.services.order_service import cancel_refund
        _, approval = _make_refund(db, amount=100.0, status="pending")
        db.commit()
        result = cancel_refund(db, approval_id=approval.id, tenant_id=1)
        assert result["status"] == "cancelled"

    def test_cancel_non_pending_rejected(self, db):
        from app.services.order_service import cancel_refund, OrderError
        _, approval = _make_refund(db, amount=100.0, status="approved")
        db.commit()
        with pytest.raises(OrderError, match="不可撤销"):
            cancel_refund(db, approval_id=approval.id, tenant_id=1)


class TestRefundGradedApproval:
    """退款分级审批契约。"""

    def test_large_amount_agent_stays_pending(self, db):
        """大额(>1000) + agent 批准 -> 保持 pending。"""
        from app.services.order_service import approve_refund
        order, approval = _make_refund(db, amount=1500.0, status="pending")
        db.commit()
        result = approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=10, tenant_id=1, role="agent")
        assert result["status"] == "pending"
        assert "二次审批" in result.get("message", "")

    def test_large_amount_admin_refunded(self, db):
        """大额(>1000) + admin 批准 -> refunded。"""
        from app.services.order_service import approve_refund
        order, approval = _make_refund(db, amount=1500.0, status="pending")
        db.commit()
        result = approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=1, tenant_id=1, role="admin")
        assert result["status"] == "approved"

    def test_small_amount_agent_refunded(self, db):
        """小额(<=1000) + agent 批准 -> refunded。"""
        from app.services.order_service import approve_refund
        order, approval = _make_refund(db, amount=500.0, status="pending")
        db.commit()
        result = approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=10, tenant_id=1, role="agent")
        assert result["status"] == "approved"


# ═══════════════════════════════════════════════════════════
# T007/T010: 退款审批的订单状态流转 + CAS 失败防护
# ═══════════════════════════════════════════════════════════
class TestRefundOrderState:
    """退款审批的订单状态回归（此前只断言审批状态，订单回退无防护）。"""

    def test_approve_moves_order_to_refunded(self, db):
        """批准退款 -> 订单 refunded。"""
        from app.models.order import Order
        from app.services.order_service import approve_refund
        order, approval = _make_refund(db, amount=100.0, status="pending")
        db.commit()
        approve_refund(db, approval_id=approval.id, decision="approved",
                       approver_id=1, tenant_id=1, role="admin")
        db.expire_all()
        assert db.get(Order, order.id).status == "refunded"

    def test_rejected_moves_order_back_to_paid(self, db):
        """拒绝退款 -> 订单回 paid（PRD 3.6 状态机回归防护）。"""
        from app.models.order import Order
        from app.services.order_service import approve_refund
        order, approval = _make_refund(db, amount=100.0, status="pending")
        db.commit()
        approve_refund(db, approval_id=approval.id, decision="rejected",
                       approver_id=1, tenant_id=1, role="admin")
        db.expire_all()
        assert db.get(Order, order.id).status == "paid"

    def test_cancel_moves_order_back_to_paid(self, db):
        """撤销退款 -> 订单回 paid。"""
        from app.models.order import Order
        from app.services.order_service import cancel_refund
        order, approval = _make_refund(db, amount=100.0, status="pending")
        db.commit()
        cancel_refund(db, approval_id=approval.id, tenant_id=1)
        db.expire_all()
        assert db.get(Order, order.id).status == "paid"

    def test_cas_failure_does_not_invoke_external_refund(self, db, monkeypatch):
        """CAS 失败（并发冲突）-> VersionConflict 且不调外部退款（T7 防双重退款核心）。"""
        from app.services import order_service as osvc
        order, approval = _make_refund(db, amount=100.0, status="pending")
        db.commit()

        invoked: list = []
        monkeypatch.setattr(osvc, "_invoke_alipay_refund",
                            lambda db_, order_, amount: invoked.append(1) or True)
        monkeypatch.setattr(osvc.order_crud, "update_status_optimistic",
                            lambda db_, order_, s: False)
        with pytest.raises(osvc.VersionConflict):
            osvc.approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=1, tenant_id=1, role="admin")
        assert invoked == [], "CAS 失败时不应调用外部退款（防止双重退款）"

    def test_external_refund_failure_keeps_refunding_and_pending(self, db, monkeypatch):
        """外部退款失败 -> RefundError，订单保持 refunding、审批回 pending（可重试）。"""
        from app.models.order import Order
        from app.models.refund_approval import RefundApproval
        from app.services import order_service as osvc
        order, approval = _make_refund(db, amount=100.0, status="pending")
        db.commit()

        monkeypatch.setattr(osvc, "_invoke_alipay_refund", lambda db_, order_, amount: False)
        with pytest.raises(osvc.RefundError):
            osvc.approve_refund(db, approval_id=approval.id, decision="approved",
                                approver_id=1, tenant_id=1, role="admin")
        db.expire_all()
        assert db.get(Order, order.id).status == "refunding"
        assert db.get(RefundApproval, approval.id).status == "pending"


# ═══════════════════════════════════════════════════════════
# T008: SSE 流式输出（User Story 2）
# ═══════════════════════════════════════════════════════════
class TestSSEStreaming:
    """SSE 流式格式契约。"""

    def test_sse_format(self, client, db, make_token, monkeypatch):
        """SSE 端点产出 data: chunk + [DONE]（真实端点 + 桩 LLM 流）。"""
        from app.crud import session as session_crud
        from app.services import agent_service
        from app.core import llm_client

        s = session_crud.create_session(db, tenant_id=1, end_user_id="eu-sse")
        db.commit()

        def fake_prepare(db, *, session_id, tenant_id, user_message):
            return {
                "need_stream": True, "content": "", "retrieval_result": [],
                "intent": "knowledge_consult", "confidence": 0.9, "sentiment": "normal",
                "should_interrupt": False, "context": [], "session": s,
                "end_user_id": "eu-sse", "final_state": {}, "elapsed_ms": 1,
            }

        def fake_finalize(db, *, session_id, tenant_id, content, prep):
            return {"message_id": 1, "role": "assistant", "content": content, "sources": [], "metadata": {}}

        monkeypatch.setattr(agent_service, "prepare_message_stream", fake_prepare)
        monkeypatch.setattr(agent_service, "finalize_message_stream", fake_finalize)
        monkeypatch.setattr(llm_client, "llm_complete_stream", lambda **kwargs: iter(["你", "好"]))

        resp = client.post(
            "/api/v1/session/message/stream",
            headers={"Authorization": f"Bearer {make_token(role='admin')}"},
            json={"session_id": s.id, "content": "你好"},
        )
        assert resp.status_code == 200
        assert "data: " in resp.text
        assert "[DONE]" in resp.text


# ═══════════════════════════════════════════════════════════
# T011: 知识库生命周期（User Story 3）
# ═══════════════════════════════════════════════════════════
class TestKnowledgeQuality:
    """知识库质量检测契约。"""

    def test_quality_check_normal(self):
        """正常文本乱码率低。"""
        from app.tasks.knowledge_tasks import check_quality
        rate = check_quality(["这是正常的中文内容，包含有意义的文字。"])
        assert rate < 0.3

    def test_quality_check_garbage(self):
        """乱码文本乱码率高。"""
        from app.tasks.knowledge_tasks import check_quality
        rate = check_quality(["\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0b\x0c\x0e\x0f"])
        assert rate > 0.3

    def test_storage_limit_under(self, db):
        """未超存储上限 -> 允许上传。"""
        from app.services.knowledge_service import check_storage_limit
        from app.models.knowledge_document import KnowledgeDocument
        db.add(KnowledgeDocument(tenant_id=1, filename="a.md", file_type="md",
                                 file_size=1024, status="ready", is_latest=True))
        db.commit()
        assert check_storage_limit(db, tenant_id=1, incoming_bytes=1024) is True

    def test_storage_limit_exceeded(self, db):
        """超存储上限 -> 拒绝。"""
        from app.services.knowledge_service import check_storage_limit
        from app.models.knowledge_document import KnowledgeDocument
        db.add(KnowledgeDocument(tenant_id=999, filename="big.md", file_type="md",
                                 file_size=600 * 1024 * 1024, status="ready", is_latest=True))
        db.commit()
        assert check_storage_limit(db, tenant_id=999) is False


# ═══════════════════════════════════════════════════════════
# T015: 计费补全（User Story 4）
# ═══════════════════════════════════════════════════════════
class TestBillingWeighting:
    """模型折算 + CSV 导出 + 逾期降级契约。"""

    def test_weighted_usage(self):
        """incr_usage 按模型权重折算。"""
        from app.services.billing_service import incr_usage
        result = incr_usage(98001, model="deepseek/deepseek-chat")
        assert result["count"] >= 1.0  # weight=1.0

    def test_csv_export(self, db):
        """CSV 导出含字段头。"""
        from app.services.billing_service import export_usage
        csv = export_usage(db, tenant_id=1)
        # 两个字段头都必须存在（原 `or` 对含任一字段的 CSV 恒真）
        assert "conversation_count" in csv and "tenant_id" in csv

    def test_overdue_check(self, db):
        """逾期检查不抛异常。"""
        from app.services.billing_service import check_overdue
        result = check_overdue(db)
        # T34: 原"or isinstance(result, dict)"对任何 dict 恒真，改为真断言
        assert isinstance(result, dict) and "degraded" in result
