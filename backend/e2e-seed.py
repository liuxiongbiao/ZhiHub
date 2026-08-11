"""e2e-seed.py — 为 UI 测试租户预置演示数据（工单/订单/账单/退款/等待会话/快捷回复）。
运行: .venv/Scripts/python.exe e2e-seed.py <tenant_id>
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.core.database import SessionLocal
from app.models.ticket import Ticket
from app.models.order import Order
from app.models.refund_approval import RefundApproval
from app.models.billing_invoice import BillingInvoice
from app.models.session import Session
from app.models.quick_reply import QuickReply
from app.models.csat import CsatEvaluation

TENANT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 0

def seed():
    db = SessionLocal()
    try:
        # 工单 x2
        for tno, title, desc, prio in [
            ("TK-E2E-0001", "登录页加载缓慢", "用户反馈登录页需要 5 秒以上才能打开", "high"),
            ("TK-E2E-0002", "套餐升级后未生效", "已支付但套餐仍显示旧版本", "urgent"),
        ]:
            if not db.query(Ticket).filter_by(ticket_no=tno).first():
                db.add(Ticket(tenant_id=TENANT_ID, ticket_no=tno, title=title, description=desc, priority=prio, status="open", phone="13800000001", category="general"))
        # paid 订单 x2（可发货）
        for ono, st, amount in [("ORD-E2E-0001", "paid", 299.0), ("ORD-E2E-0002", "paid", 99.0)]:
            o = db.query(Order).filter_by(order_no=ono).first()
            if not o:
                db.add(Order(tenant_id=TENANT_ID, order_no=ono, amount=amount, paid_amount=amount, status=st, version=1, phone="13800000001"))
            else:
                # 重复测试：重置为 paid 并清空物流单号
                o.status = st
                o.tracking_no = None
                o.version = 1
        db.flush()
        # 退款审批（pending，关联 ORD-E2E-0001）
        o1 = db.query(Order).filter_by(order_no="ORD-E2E-0001").first()
        if o1 and not db.query(RefundApproval).filter_by(order_id=o1.id).first():
            db.add(RefundApproval(order_id=o1.id, amount=100.0, reason="测试退款申请", client_request_id=f"cid-e2e-{o1.id}", status="pending"))
        # 账单 x2（unpaid + paid）
        for period, status, base, over in [("2026-08", "unpaid", 100.0, 12.5), ("2026-07", "paid", 100.0, 0.0)]:
            if not db.query(BillingInvoice).filter_by(tenant_id=TENANT_ID, period=period).first():
                db.add(BillingInvoice(tenant_id=TENANT_ID, period=period, base_fee=base, overage_fee=over, status=status))
        # waiting_human 会话（坐席工作台认领/退回/Trace）
        sess = db.query(Session).filter_by(tenant_id=TENANT_ID, end_user_id="eu-e2e-001").first()
        if not sess:
            db.add(Session(tenant_id=TENANT_ID, status="waiting_human", intent="order_query", confidence=0.31, sentiment="unhappy", end_user_id="eu-e2e-001"))
        else:
            # 重复测试：重置为 waiting_human 并释放认领
            sess.status = "waiting_human"
            sess.assignee_id = None
        # closed 会话（CSAT 评价测试，A001）
        csat_sess = db.query(Session).filter_by(tenant_id=TENANT_ID, end_user_id="eu-e2e-csat").first()
        if not csat_sess:
            db.add(Session(tenant_id=TENANT_ID, status="closed", intent="knowledge_consult", confidence=0.9, sentiment="normal", end_user_id="eu-e2e-csat"))
        else:
            csat_sess.status = "closed"
            # 重复测试：删除该会话已有 CSAT，允许重新验证 201 首次提交
            db.query(CsatEvaluation).filter(CsatEvaluation.session_id == csat_sess.id).delete()
        # 快捷回复 x2
        for content, shortcut in [("您好，正在为您查询，请稍候。", "chaxun"), ("感谢您的反馈，我们会尽快处理。", "ganxie")]:
            if not db.query(QuickReply).filter_by(tenant_id=TENANT_ID, shortcut=shortcut).first():
                db.add(QuickReply(tenant_id=TENANT_ID, content=content, shortcut=shortcut))
        db.commit()
        print(f"[OK] seeded tenant {TENANT_ID}: tickets/orders/refund/invoices/session/quick-replies")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
