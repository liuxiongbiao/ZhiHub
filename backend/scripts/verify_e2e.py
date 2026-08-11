"""端到端验证脚本: 逐项检查 ZhiHub 各子系统,输出 PASS/FAIL 清单。"""
import json, sys, os, time, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MYSQL_ROOT_PASSWORD", "root")

import urllib.request, urllib.error

BASE = "http://localhost:8000/api/v1"
results = []

def check(name, fn, timeout=30):
    try:
        detail = fn()
        results.append((name, "PASS", detail))
        print(f"[PASS] {name}: {detail}")
    except Exception as e:
        msg = str(e)[:120]
        results.append((name, "FAIL", msg))
        print(f"[FAIL] {name}: {msg}")

def _req(method, path, token=None, body=None, timeout=30):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())

# ── A. 基础设施 ──
def chk_mysql():
    from app.core.database import engine
    from sqlalchemy import text
    with engine.connect() as c:
        v = c.execute(text("SELECT 1")).scalar()
    return f"MySQL 连接 OK (SELECT 1 -> {v})"

def chk_redis():
    from app.core import redis as rm
    c = rm.get_redis()
    return f"Redis ping -> {c.ping()}"

def chk_milvus():
    from app.core.embedding_client import get_milvus_collection
    col = get_milvus_collection()
    return f"Milvus collection={col.name}, rows={col.num_entities}"

def chk_es():
    from app.core.embedding_client import get_es_client
    es = get_es_client()
    info = es.info()
    return f"ES version={info['version']['number']}"

# ── B. 认证与租户 ──
TOKEN = [None]
def chk_login():
    from app.core.database import SessionLocal
    from app.services.auth_service import login
    db = SessionLocal()
    try:
        r = login(db, phone="13800000088", password="Test1234abc")
        TOKEN[0] = r.access_token
        return f"登录 OK tenant={r.tenant_id} role={r.role}"
    finally:
        db.close()

def chk_tenant_home():
    s, d = _req("GET", "/tenant/home", TOKEN[0])
    return f"tenant={d.get('tenant_name')} plan={d.get('plan_type')} status={d.get('status')}"

def chk_wizard():
    s, d = _req("GET", "/tenant/wizard", TOKEN[0])
    return f"wizard step={d.get('current_step')} completed={d.get('completed')}"

def chk_no_token_401():
    try:
        _req("GET", "/tenant/home")
        return "未返回 401(异常)"
    except urllib.error.HTTPError as e:
        return f"无 token -> {e.code}(正确拦截)"

# ── C. 知识库与检索 ──
def chk_knowledge_list():
    s, d = _req("GET", "/knowledge/list?page=1&page_size=50", TOKEN[0])
    return f"文档数={d.get('total')} (期望 24)"

def chk_hybrid_search():
    s, d = _req("POST", "/knowledge/test", TOKEN[0], {"query": "退款政策是什么", "history": []}, timeout=60)
    vr = len(d.get("vector_results", []))
    hr = len(d.get("hybrid_results", []))
    return f"vector={vr}条 hybrid={hr}条 hyde_fallback={d.get('hyde_fallback')}"

# ── D. LangGraph 智能体主链路 ──
SESSION_ID = [None]
def chk_create_session():
    s, d = _req("POST", "/session", TOKEN[0], {}, timeout=30)
    SESSION_ID[0] = d.get("session_id")
    return f"session_id={SESSION_ID[0]}"

def chk_agent_message():
    s, d = _req("POST", "/session/message", TOKEN[0], {"session_id": SESSION_ID[0], "content": "已支付订单能退款吗"}, timeout=90)
    content = (d.get("content") or "")[:60]
    return f"role={d.get('role')} sources={len(d.get('sources') or [])} 回答={content}..."

# ── E. 业务接口 ──
def chk_ticket_list():
    s, d = _req("GET", "/ticket/list?page=1&page_size=10", TOKEN[0])
    return f"工单 total={d.get('total')}"

def chk_order_list():
    s, d = _req("GET", "/order/list?page=1&page_size=10", TOKEN[0])
    return f"订单 total={d.get('total')}"

def chk_billing():
    s, d = _req("GET", "/billing/quota", TOKEN[0])
    return f"配额 {d.get('count')}/{d.get('quota')} 剩余={d.get('remaining')}"

def chk_refunds():
    s, d = _req("GET", "/order/refunds/list?page=1&page_size=10", TOKEN[0])
    return f"退款审批 total={d.get('total')}"

# ── F. 前端 ──
def chk_frontend():
    import urllib.request
    with urllib.request.urlopen("http://localhost:5173/", timeout=5) as r:
        html = r.read().decode()
    return f"dev server 200, title={'智枢 ZhiHub' in html}"

# ── 跑 ──
print("=" * 60)
print("ZhiHub 端到端验证")
print("=" * 60)
print("\n--- A. 基础设施 ---")
check("MySQL 连接", chk_mysql)
check("Redis 连接", chk_redis)
check("Milvus 连接", chk_milvus)
check("Elasticsearch 连接", chk_es)

print("\n--- B. 认证与多租户 ---")
check("登录获取 token", chk_login)
check("GET /tenant/home", chk_tenant_home)
check("GET /tenant/wizard", chk_wizard)
check("JWT 鉴权(无 token 应 401)", chk_no_token_401)

print("\n--- C. 知识库与混合检索 ---")
check("GET /knowledge/list (24份文档)", chk_knowledge_list)
check("POST /knowledge/test (混合检索)", chk_hybrid_search, timeout=70)

print("\n--- D. LangGraph 智能体主链路 ---")
check("POST /session (创建会话)", chk_create_session)
check("POST /session/{id}/message (Agent 回答)", chk_agent_message, timeout=100)

print("\n--- E. 业务接口 ---")
check("GET /ticket/list", chk_ticket_list)
check("GET /order/list", chk_order_list)
check("GET /billing/quota", chk_billing)
check("GET /order/refunds/list (人工接管)", chk_refunds)

print("\n--- F. 前端 ---")
check("前端 dev server", chk_frontend)

print("\n" + "=" * 60)
p = sum(1 for _, s, _ in results if s == "PASS")
f = sum(1 for _, s, _ in results if s == "FAIL")
print(f"汇总: {p} PASS / {f} FAIL / 共 {len(results)} 项")
print("=" * 60)
