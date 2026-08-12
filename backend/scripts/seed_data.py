"""
backend/scripts/seed_data.py


核心设计:
  工单与订单统一绑定手机号 13800000001（双因子 happy-path 用此号后4位匹配通过）。
  幂等写入：已存在的记录跳过或补 phone 列，可重复执行不报错。
  ORD-20240001 置为 refunding + 挂一条 RefundApproval，给 approve_refund 测试提供数据。

运行: python scripts/seed_data.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal
from app.crud.ticket import create_ticket as _create_ticket
from app.crud.order import create_refund_approval
from app.models.order import Order
from app.models.ticket import Ticket
from app.models.refund_approval import RefundApproval

def seed():
    db = SessionLocal()
    try:
        # 工单（006-mcp-security: 绑定终端用户手机号，供双因子校验比对后4位）
        # phone 统一绑定为测试账号 13800000001，双因子 happy-path 用此号查询通过
        tickets = [
            ("TK-20240001", "登录页面加载缓慢", "用户反馈登录页需要5秒以上才能打开", "high"),
            ("TK-20240002", "套餐升级后未生效", "已支付但套餐仍显示旧版本", "urgent"),
            ("TK-20240003", "订单地址修改", "需要将收货地址从北京改为上海", "medium"),
            ("TK-20240004", "发票开具申请", "需要开具增值税专用发票", "low"),
            ("TK-20240005", "账号异常登录提醒", "收到异地登录通知，请协助排查", "high"),
        ]
        for tno, title, desc, priority in tickets:
            existing = db.query(Ticket).filter_by(ticket_no=tno).first()
            if not existing:
                t = _create_ticket(db, tenant_id=1, ticket_no=tno, title=title, description=desc, priority=priority)
                t.phone = "13800000001"
            elif existing.phone is None:
                existing.phone = "13800000001"

        # 订单（同样绑定手机号 13800000001）
        orders = [
            ("ORD-20240001", 299.0, 299.0, "paid"),
            ("ORD-20240002", 599.0, 569.0, "shipped"),
            ("ORD-20240003", 199.0, 199.0, "completed"),
            ("ORD-20240004", 89.0, 89.0, "paid"),
            ("ORD-20240005", 1299.0, 1299.0, "created"),
        ]
        for ono, amount, paid, status in orders:
            existing = db.query(Order).filter_by(order_no=ono).first()
            if not existing:
                order = Order(tenant_id=1, order_no=ono, amount=amount, paid_amount=paid, status=status, phone="13800000001")
                db.add(order)
            elif existing.phone is None:
                existing.phone = "13800000001"

        # 退款审批（给退款审批页提供数据）
        order1 = db.query(Order).filter_by(order_no="ORD-20240001").first()
        if order1:
            # 模拟 request_refund 后的订单状态（paid -> refunding），供 approve_refund 测试
            if order1.status == "paid":
                order1.status = "refunding"
            existing_refund = db.query(RefundApproval).filter_by(order_id=order1.id).first()
            if not existing_refund:
                create_refund_approval(
                    db, order_id=order1.id, amount=100, reason="测试退款审批",
                    client_request_id="seed-refund-001",
                )

        db.commit()
        print(f"Seed done: 5 tickets + 5 orders + 1 refund approval created")
    except Exception as e:
        db.rollback()
        print(f"Seed failed: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    # Need the model import for querying
    from app.models.ticket import Ticket
    from app.models.order import Order
    seed()
