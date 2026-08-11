"""
backend/tests/contracts/test_audit.py

职责: 测试 PRD 2.1 系统审计日志契约 -- 关键操作埋点 + 端点查询 + 租户隔离
对应日程: handover-and-plan #28

测试内容:
  - TestRefundApproveAudit: approve_refund 成功后 audit_logs 有 refund_approve 记录
  - TestRefundCancelAudit: cancel_refund 成功后 audit_logs 有 refund_cancel 记录
  - TestLoginAudit: login 成功后 audit_logs 有 login 记录
  - TestLogoutAudit: logout 后 audit_logs 有 logout 记录
  - TestWizardAdvanceAudit: advance_wizard 后 audit_logs 有 wizard_advance 记录
  - TestPaymentCallbackAudit: handle_payment_callback 后 audit_logs 有 payment 记录
  - TestAuditTenantIsolation: 审计日志按 tenant_id 隔离，跨租户不可见
  - TestAuditLogEndpoint: GET /api/v1/tenant/audit-logs 分页查询（admin）
  - TestAuditLogEndpointForbidden: agent 角色访问端点 -> 403

设计决策:
  - 审计日志与业务操作同事务: log_audit 只 add+flush 不 commit，由各 service 的
    commit 统一提交，保证操作成功才有审计记录（原子性）
  - 登录/登出无 DB 写入，需显式 commit 落审计记录
  - 租户隔离: audit_logs 表带 tenant_id 索引，端点查询强制 WHERE tenant_id=?
"""

import pytest
from sqlalchemy import func, select

from app.core.security import hash_password
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud
from app.models.audit_log import AuditLog
from app.models.billing_invoice import BillingInvoice
from app.models.order import Order
from app.models.refund_approval import RefundApproval
from app.models.tenant import Tenant


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════
def _make_refunding_order(db, *, tenant_id=1, order_no="ORD-AUD-001", amount=100.0):
    """创建一个 refunding 状态的订单 + pending 退款审批，供审批/撤销测试。"""
    order = Order(
        tenant_id=tenant_id, order_no=order_no, amount=299.0,
        paid_amount=299.0, status="refunding",
    )
    db.add(order)
    db.flush()
    approval = RefundApproval(
        order_id=order.id, amount=amount, reason="test audit",
        client_request_id=f"cid-aud-{order.id}", status="pending",
    )
    db.add(approval)
    db.flush()
    return order, approval


def _make_invoice(db, *, tenant_id=1, period="2026-08", status="unpaid"):
    """创建一条账单供支付回调测试。"""
    inv = BillingInvoice(
        tenant_id=tenant_id, period=period, base_fee=99.0,
        overage_fee=0.0, status=status,
    )
    db.add(inv)
    db.flush()
    return inv


def _patch_alipay(monkeypatch, *, verify=True, refund_ok=True):
    """monkeypatch alipay_client 单例方法，返回受控结果。"""
    from app.core import alipay_client as ac

    monkeypatch.setattr(ac.AlipayClient, "create_payment", lambda self, *a, **k: "mock")
    monkeypatch.setattr(ac.AlipayClient, "verify_callback", lambda self, form_data: verify)
    monkeypatch.setattr(ac.AlipayClient, "refund", lambda self, trade_no, amount: refund_ok)


def _make_tenant_with_admin(db, *, tenant_id=1, wizard_step="basic"):
    """建租户 + admin 成员，供向导/登录审计测试。"""
    t = Tenant(name="Acme", plan_type="trial", seat_limit=5, status="active", wizard_step=wizard_step)
    db.add(t)
    db.flush()
    user = user_crud.create(
        db, phone="13800000000", password_hash=hash_password("abc12345"), role_default="admin"
    )
    tenant_crud.add_member(db, tenant_id=t.id, user_id=user.id, role="admin")
    db.commit()
    return t, user


def _count_audit_logs(db, *, tenant_id=None, action=None):
    """查询审计日志条数（可按 tenant_id/action 过滤）。"""
    stmt = select(func.count()).select_from(AuditLog)
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    return db.execute(stmt).scalar() or 0


# ═══════════════════════════════════════════════════════════
# T1: 退款审批 -> audit_logs
# ═══════════════════════════════════════════════════════════
class TestRefundApproveAudit:
    """approve_refund 成功后应写入 refund_approve 审计记录。"""

    def test_approve_refund_creates_audit(self, db, monkeypatch):
        _patch_alipay(monkeypatch, refund_ok=True)
        from app.services.order_service import approve_refund

        order, approval = _make_refunding_order(db, amount=100.0)
        db.commit()

        approve_refund(
            db, approval_id=approval.id, decision="approved",
            approver_id=2, tenant_id=1, role="admin",
        )

        count = _count_audit_logs(db, tenant_id=1, action="refund_approve")
        assert count == 1

    def test_approve_refund_audit_fields(self, db, monkeypatch):
        """审计记录应包含正确的 target_type/target_id/tenant_id。"""
        _patch_alipay(monkeypatch, refund_ok=True)
        from app.services.order_service import approve_refund

        order, approval = _make_refunding_order(db, amount=100.0)
        db.commit()

        approve_refund(
            db, approval_id=approval.id, decision="approved",
            approver_id=2, tenant_id=1, role="admin",
        )

        log = db.execute(
            select(AuditLog).where(AuditLog.action == "refund_approve")
        ).scalar_one()

        assert log.tenant_id == 1
        assert log.user_id == 2
        assert log.target_type == "refund_approval"
        assert log.target_id == str(approval.id)

    def test_rejected_refund_also_audited(self, db, monkeypatch):
        """rejected 决策也应记录审计（不走支付宝退款）。"""
        _patch_alipay(monkeypatch)
        from app.services.order_service import approve_refund

        order, approval = _make_refunding_order(db, amount=50.0)
        db.commit()

        approve_refund(
            db, approval_id=approval.id, decision="rejected",
            approver_id=2, tenant_id=1, role="admin",
        )

        count = _count_audit_logs(db, action="refund_approve")
        assert count == 1


# ═══════════════════════════════════════════════════════════
# T2: 退款撤销 -> audit_logs
# ═══════════════════════════════════════════════════════════
class TestRefundCancelAudit:
    """cancel_refund 成功后应写入 refund_cancel 审计记录。"""

    def test_cancel_refund_creates_audit(self, db):
        from app.services.order_service import cancel_refund

        order, approval = _make_refunding_order(db, amount=100.0)
        db.commit()

        cancel_refund(db, approval_id=approval.id, tenant_id=1)

        count = _count_audit_logs(db, tenant_id=1, action="refund_cancel")
        assert count == 1

    def test_cancel_refund_audit_target(self, db):
        from app.services.order_service import cancel_refund

        order, approval = _make_refunding_order(db, amount=100.0)
        db.commit()

        cancel_refund(db, approval_id=approval.id, tenant_id=1)

        log = db.execute(
            select(AuditLog).where(AuditLog.action == "refund_cancel")
        ).scalar_one()
        assert log.target_type == "refund_approval"
        assert log.target_id == str(approval.id)


# ═══════════════════════════════════════════════════════════
# T3: 登录 -> audit_logs
# ═══════════════════════════════════════════════════════════
class TestLoginAudit:
    """login 成功后应写入 login 审计记录。"""

    def test_login_creates_audit(self, db):
        _make_tenant_with_admin(db)
        from app.services.auth_service import login

        result = login(db, phone="13800000000", password="abc12345")

        count = _count_audit_logs(db, tenant_id=result.tenant_id, action="login")
        assert count == 1

    def test_login_audit_user_id(self, db):
        _make_tenant_with_admin(db)
        from app.services.auth_service import login

        result = login(db, phone="13800000000", password="abc12345")

        log = db.execute(
            select(AuditLog).where(AuditLog.action == "login")
        ).scalar_one()
        assert log.user_id == result.user_id
        assert log.target_type == "user"

    def test_login_fail_no_audit(self, db):
        """登录失败不应写审计记录（仅成功登录才记录）。"""
        _make_tenant_with_admin(db)
        from app.services.auth_service import login, InvalidCredentials

        with pytest.raises(InvalidCredentials):
            login(db, phone="13800000000", password="wrong-password")

        count = _count_audit_logs(db, action="login")
        assert count == 0


# ═══════════════════════════════════════════════════════════
# T4: 登出 -> audit_logs
# ═══════════════════════════════════════════════════════════
class TestLogoutAudit:
    """logout 后应写入 logout 审计记录。"""

    def test_logout_creates_audit(self, db):
        t, user = _make_tenant_with_admin(db)
        from app.services.auth_service import logout

        logout(
            db, jti="test-jti-001", exp=9999999999,
            user_id=user.id, tenant_id=t.id, role="admin",
        )

        count = _count_audit_logs(db, tenant_id=t.id, action="logout")
        assert count == 1

    def test_logout_audit_fields(self, db):
        t, user = _make_tenant_with_admin(db)
        from app.services.auth_service import logout

        logout(
            db, jti="test-jti-002", exp=9999999999,
            user_id=user.id, tenant_id=t.id, role="admin",
        )

        log = db.execute(
            select(AuditLog).where(AuditLog.action == "logout")
        ).scalar_one()
        assert log.user_id == user.id
        assert log.tenant_id == t.id


# ═══════════════════════════════════════════════════════════
# T5: 向导推进 -> audit_logs
# ═══════════════════════════════════════════════════════════
class TestWizardAdvanceAudit:
    """advance_wizard 后应写入 wizard_advance 审计记录。"""

    def test_wizard_advance_creates_audit(self, db):
        t, _ = _make_tenant_with_admin(db, wizard_step="basic")
        from app.services.tenant_service import advance_wizard

        advance_wizard(db, t.id)

        count = _count_audit_logs(db, tenant_id=t.id, action="wizard_advance")
        assert count == 1

    def test_wizard_advance_multiple_steps(self, db):
        """每步推进都产生一条审计记录。"""
        t, _ = _make_tenant_with_admin(db, wizard_step="basic")
        from app.services.tenant_service import advance_wizard

        advance_wizard(db, t.id)  # basic -> knowledge
        advance_wizard(db, t.id)  # knowledge -> mcp

        count = _count_audit_logs(db, tenant_id=t.id, action="wizard_advance")
        assert count == 2


# ═══════════════════════════════════════════════════════════
# T6: 支付回调 -> audit_logs
# ═══════════════════════════════════════════════════════════
class TestPaymentCallbackAudit:
    """handle_payment_callback 成功后应写入 payment 审计记录。"""

    def test_payment_callback_creates_audit(self, db, monkeypatch):
        _patch_alipay(monkeypatch, verify=True)
        from app.services.billing_service import handle_payment_callback

        inv = _make_invoice(db)
        db.commit()

        form = {
            "trade_no": "ALIPAY-AUD-001",
            "out_trade_no": f"ALI-{inv.id}-abcd1234",
            "trade_status": "TRADE_SUCCESS",
        }
        handle_payment_callback(db, form_data=form, tenant_id=1)

        count = _count_audit_logs(db, tenant_id=1, action="payment")
        assert count == 1

    def test_payment_callback_audit_target(self, db, monkeypatch):
        _patch_alipay(monkeypatch, verify=True)
        from app.services.billing_service import handle_payment_callback

        inv = _make_invoice(db)
        db.commit()

        form = {
            "trade_no": "ALIPAY-AUD-002",
            "out_trade_no": f"ALI-{inv.id}-abcd1234",
            "trade_status": "TRADE_SUCCESS",
        }
        handle_payment_callback(db, form_data=form, tenant_id=1)

        log = db.execute(
            select(AuditLog).where(AuditLog.action == "payment")
        ).scalar_one()
        assert log.target_type == "billing_invoice"
        assert log.target_id == str(inv.id)

    def test_payment_verify_fail_no_audit(self, db, monkeypatch):
        """验签失败不应写审计记录。"""
        _patch_alipay(monkeypatch, verify=False)
        from app.services.billing_service import handle_payment_callback, PaymentError

        inv = _make_invoice(db)
        db.commit()

        form = {
            "trade_no": "ALIPAY-BAD-AUD",
            "out_trade_no": f"ALI-{inv.id}-abcd1234",
            "trade_status": "TRADE_SUCCESS",
        }
        with pytest.raises(PaymentError):
            handle_payment_callback(db, form_data=form, tenant_id=1)

        count = _count_audit_logs(db, action="payment")
        assert count == 0


# ═══════════════════════════════════════════════════════════
# T7: 租户隔离
# ═══════════════════════════════════════════════════════════
class TestAuditTenantIsolation:
    """审计日志按 tenant_id 隔离，跨租户互不可见。"""

    def test_cross_tenant_audit_isolated(self, db):
        """tenant 1 的审计记录不会被 tenant 2 查到。"""
        log1 = AuditLog(tenant_id=1, user_id=1, action="login", target_type="user", target_id="1")
        log2 = AuditLog(tenant_id=2, user_id=2, action="login", target_type="user", target_id="2")
        db.add_all([log1, log2])
        db.commit()

        from sqlalchemy import select

        tenant1_logs = db.execute(
            select(AuditLog).where(AuditLog.tenant_id == 1)
        ).scalars().all()
        tenant2_logs = db.execute(
            select(AuditLog).where(AuditLog.tenant_id == 2)
        ).scalars().all()

        assert len(tenant1_logs) == 1
        assert tenant1_logs[0].tenant_id == 1
        assert len(tenant2_logs) == 1
        assert tenant2_logs[0].tenant_id == 2


# ═══════════════════════════════════════════════════════════
# T8: 审计日志查询端点（admin 分页）
# ═══════════════════════════════════════════════════════════
@pytest.fixture()
def tenant_for_endpoint(db):
    """建一个 id=1 的 active 租户，供端点测试的 require_tenant() 依赖通过。"""
    t = Tenant(name="Acme", plan_type="trial", seat_limit=5, status="active", wizard_step="done")
    db.add(t)
    db.commit()
    return t


class TestAuditLogEndpoint:
    """GET /api/v1/tenant/audit-logs 端点契约。"""

    def test_list_audit_logs_admin(self, client, db, make_token, tenant_for_endpoint):
        """admin 可查看本租户审计日志列表。"""
        for i in range(3):
            db.add(AuditLog(
                tenant_id=1, user_id=1, action="login",
                target_type="user", target_id="1",
            ))
        db.commit()

        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.get(
            "/api/v1/tenant/audit-logs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        assert body["page"] == 1
        # 每条记录含必要字段
        item = body["items"][0]
        assert "id" in item
        assert "action" in item
        assert "tenant_id" in item
        assert "created_at" in item

    def test_list_audit_logs_pagination(self, client, db, make_token, tenant_for_endpoint):
        """分页参数生效: page=1, page_size=2 返回 2 条，total=5。"""
        for i in range(5):
            db.add(AuditLog(
                tenant_id=1, user_id=1, action="login",
                target_type="user", target_id="1",
            ))
        db.commit()

        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.get(
            "/api/v1/tenant/audit-logs?page=1&page_size=2",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["page"] == 1
        assert body["page_size"] == 2

    def test_list_audit_logs_tenant_isolated(self, client, db, make_token, tenant_for_endpoint):
        """端点只返回当前租户的审计日志，不含其他租户。"""
        db.add(AuditLog(tenant_id=1, user_id=1, action="login", target_type="user", target_id="1"))
        db.add(AuditLog(tenant_id=2, user_id=2, action="login", target_type="user", target_id="2"))
        db.commit()

        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.get(
            "/api/v1/tenant/audit-logs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["tenant_id"] == 1

    def test_list_audit_logs_ordered_desc(self, client, db, make_token, tenant_for_endpoint):
        """审计日志按 created_at 倒序（最新在前）。"""
        import time
        from datetime import datetime

        log_old = AuditLog(tenant_id=1, user_id=1, action="login", target_type="user", target_id="1")
        db.add(log_old)
        db.commit()
        time.sleep(0.05)  # 确保 created_at 有差异

        log_new = AuditLog(tenant_id=1, user_id=1, action="logout", target_type="user", target_id="1")
        db.add(log_new)
        db.commit()

        token = make_token(user_id=1, tenant_id=1, role="admin")
        resp = client.get(
            "/api/v1/tenant/audit-logs",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["items"][0]["action"] == "logout"


# ═══════════════════════════════════════════════════════════
# T9: 角色权限
# ═══════════════════════════════════════════════════════════
class TestAuditLogEndpointForbidden:
    """非 admin 角色不可访问审计日志端点。"""

    def test_agent_forbidden(self, client, db, make_token):
        token = make_token(user_id=2, tenant_id=1, role="agent")
        resp = client.get(
            "/api/v1/tenant/audit-logs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client):
        """未认证请求 -> 401。"""
        resp = client.get("/api/v1/tenant/audit-logs")
        assert resp.status_code == 401
