"""第二轮修复真实环境冒烟:迁移后真实库 + 真实 Redis + 修复关键路径(H1/H2/M6/M7/M1-M3/L5)

验证项:
  1. 应用在迁移后真实库(d16-d20)上启动
  2. 注册+登录走真实 Redis(M1/M2/M3 新代码)
  3. MCP request_refund:幂等 namespace=order_id + Decimal 比较 + 状态机 + 乐观锁
  4. 幂等重试返回与首次一致结构(L5)
  5. 审批 CAS 先行(T7)+ mock alipay
  6. M6:同一账单两个 trade_no 回调,第二个不覆盖 payment_trade_no
  7. H1:聚合任务当月过滤不污染(写入 usage_records 检查)
"""
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend 根

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.refund_approval import RefundApproval  # noqa: E402
from app.models.billing_invoice import BillingInvoice  # noqa: E402
from sqlalchemy import select  # noqa: E402

PASS = FAIL = 0


def step(name, ok, detail=""):
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += (not ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


c = TestClient(app)
r = c.get("/health")
step("health(迁移后真实库)", r.status_code == 200, f"HTTP {r.status_code}")

# 1. 注册 + 登录(真实 Redis)
phone = "138" + "".join(__import__("random").choices("0123456789", k=8))
r = c.post("/api/v1/auth/register", json={"phone": phone, "password": "Test1234abc", "tenant_name": "smoke-r2"})
step("register", r.status_code in (201, 409), f"HTTP {r.status_code}")
r = c.post("/api/v1/auth/login", json={"phone": phone, "password": "Test1234abc"})
step("login(真实 Redis, M1/M2/M3)", r.status_code == 200, f"HTTP {r.status_code}")
token = r.json()["access_token"]
tenant_id = r.json()["tenant_id"]
headers = {"Authorization": f"Bearer {token}"}

# 2. seed paid 订单
db = SessionLocal()
o = Order(tenant_id=tenant_id, order_no=f"ORD-SMOKE-{uuid.uuid4().hex[:6]}",
          amount=Decimal("88.00"), paid_amount=Decimal("88.00"), status="paid")
db.add(o)
db.commit()
order_id, order_no = o.id, o.order_no
db.close()
step("seed paid order(真实库)", bool(order_id), f"id={order_id}")

# 3. MCP request_refund(幂等 namespace=order_id + Decimal + 乐观锁)
cid = f"cid-{uuid.uuid4().hex[:10]}"
args = {"order_no": order_no, "amount": 50.0, "reason": "smoke", "client_request_id": cid}
r = c.post("/api/v1/mcp/tools/call", json={"name": "request_refund", "arguments": args}, headers=headers)
body = r.json().get("result", {})
step("mcp request_refund", r.status_code == 200 and body.get("id"), f"HTTP {r.status_code} body={r.text[:200]}")
approval_id = body.get("id")

# 4. 幂等重试(L5: 结构一致)
r2 = c.post("/api/v1/mcp/tools/call", json={"name": "request_refund", "arguments": args}, headers=headers)
res2 = r2.json().get("result", {})
step("幂等重试结构一致(L5)", res2.get("cached") is True and res2.get("id") == approval_id and res2.get("amount") == 50.0,
     f"body={r2.text[:200]}")

# 5. 审批(CAS 先行 T7 + mock alipay)
r = c.post(f"/api/v1/order/refunds/{approval_id}/approve", json={"decision": "approved"}, headers=headers)
step("approve refund(CAS先行)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:150]}")

db = SessionLocal()
o2 = db.execute(select(Order).where(Order.id == order_id)).scalar_one()
ap = db.execute(select(RefundApproval).where(RefundApproval.id == approval_id)).scalar_one()
db.close()
step("order->refunded", o2.status == "refunded", f"status={o2.status}")
step("approval->approved", ap.status == "approved", f"status={ap.status}")

# 6. M6: 同一账单两个 trade_no, 第二个不覆盖 payment_trade_no
from app.services.billing_service import generate_invoice  # noqa: E402
db = SessionLocal()
inv = generate_invoice(db, tenant_id=tenant_id, period="2026-08")
db.commit()
invoice_id = inv["id"]
db.close()
r = c.post("/api/v1/billing/alipay/notify",
           data={"trade_no": "T-SMOKE-1", "out_trade_no": f"ALI-{invoice_id}-aaaa1111",
                 "trade_status": "TRADE_SUCCESS", "sign": "x"})
step("notify#1(首次入账)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:80]}")
r = c.post("/api/v1/billing/alipay/notify",
           data={"trade_no": "T-SMOKE-2", "out_trade_no": f"ALI-{invoice_id}-bbbb2222",
                 "trade_status": "TRADE_SUCCESS", "sign": "x"})
db = SessionLocal()
inv2 = db.execute(select(BillingInvoice).where(BillingInvoice.id == invoice_id)).scalar_one()
db.close()
step("M6: 第二个 trade_no 不覆盖", inv2.payment_trade_no == "T-SMOKE-1" and inv2.status == "paid",
     f"trade_no={inv2.payment_trade_no} status={inv2.status}")

# 7. H1: 聚合任务只处理当月 key
from app.core.redis import get_redis  # noqa: E402
import app.tasks.billing_tasks as bt  # noqa: E402
r_redis = get_redis()
cur_month = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m")
last_month = (__import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(day=1) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m")
r_redis.set(f"billing:quota:{tenant_id}:{last_month}", "150")
r_redis.set(f"billing:quota:{tenant_id}:{cur_month}", "30")
result = bt.aggregate_daily_usage()
from app.models.usage_record import UsageRecord  # noqa: E402
db = SessionLocal()
rec = db.execute(select(UsageRecord).where(UsageRecord.tenant_id == tenant_id)).scalar_one_or_none()
db.close()
step("H1: 聚合用当月增量非上月总量", result["status"] == "ok" and rec and rec.conversation_count == 30,
     f"day_count={rec.conversation_count if rec else None} result={result}")

print(f"\n=== {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
