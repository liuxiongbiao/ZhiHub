"""真实 MySQL HTTP 端到端 smoke（不依赖 Docker / 真实第三方 key）。

设计:
  - fakeredis 替换 core.redis._redis_client（懒加载单例，拦截所有 Redis 调用，
    无需真实 Redis 容器）。
  - TestClient 覆盖 get_db -> 真实 MySQL SessionLocal，中间件链路（auth/tenant/
    ratelimit/accesslog）保持真实，从而测到完整 HTTP 请求链而非仅路由层。
  - 修完 phone VARCHAR(256) + tenants widget 两列 schema 漂移后，验证真实 DB 上
    HTTP 全链路通畅（现有 e2e 全跑在 SQLite 内存库，从未打过真实 MySQL）。

运行: .venv/Scripts/python.exe scripts/real_mysql_smoke.py
"""

import os
import sys
import time

# 确保能 import app.*（脚本位于 scripts/，需把 backend 根加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis

from app.core import redis as redis_module

# 必须在 import app.main 前替换 redis 单例（get_redis 懒加载，首次调用才连真实 Redis）
redis_module._redis_client = fakeredis.FakeStrictRedis(decode_responses=True)

from app.core.database import SessionLocal, get_db  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _real_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _real_db

_results: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}: {detail}")


def _ensure_account(c, phone, password="Test1234abc", tenant_name="smoke"):
    """注册（已存在则忽略 409）+ 登录，返回 (token, tenant_id)。"""
    c.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": password, "tenant_name": tenant_name},
    )
    r = c.post("/api/v1/auth/login", json={"phone": phone, "password": password})
    b = r.json()
    return b.get("access_token"), b.get("tenant_id")


# ============ A1 认证 HTTP 全链路 ============
def a1(c):
    phone = "13900000001"
    r = c.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test1234abc", "tenant_name": "smoke-A1"},
    )
    step("A1 register", r.status_code in (201, 409), f"HTTP {r.status_code} (409=重跑已存在)")

    r = c.post("/api/v1/auth/login", json={"phone": phone, "password": "Test1234abc"})
    step("A1 login", r.status_code == 200, f"HTTP {r.status_code}")
    b = r.json() if r.status_code == 200 else {}
    token, tenant_id = b.get("access_token"), b.get("tenant_id")
    H = {"Authorization": f"Bearer {token}"}

    r = c.get("/api/v1/tenant/home", headers=H)
    step("A1 home", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.post("/api/v1/auth/logout", headers=H)
    step("A1 logout", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.get("/api/v1/tenant/home", headers=H)
    step("A1 logout后旧token失效(401)", r.status_code == 401, f"HTTP {r.status_code}")

    agent_token = create_access_token(
        subject="9999", extra_claims={"tenant_id": tenant_id or 1, "role": "agent"}
    )
    r = c.get("/api/v1/tenant/billing", headers={"Authorization": f"Bearer {agent_token}"})
    step("A1 agent越权billing(403)", r.status_code == 403, f"HTTP {r.status_code}")


# ============ A2 多登录方式 ============
def a2(c):
    phone = "13900000002"
    _ensure_account(c, phone, tenant_name="smoke-A2")

    # 短信验证码：demo 模式直接返回 code
    r = c.post("/api/v1/auth/sms/send", json={"phone": phone})
    step("A2 sms/send", r.status_code == 200, f"HTTP {r.status_code}")
    code = r.json().get("code") if r.status_code == 200 else None

    r = c.post("/api/v1/auth/login/sms", json={"phone": phone, "code": code})
    step("A2 login/sms", r.status_code == 200, f"HTTP {r.status_code}")

    # 邮箱注册 + 登录
    email = "a2@smoke.test"
    r = c.post(
        "/api/v1/auth/register/email",
        json={"email": email, "password": "Test1234abc", "tenant_name": "smoke-A2e", "plan_type": "trial"},
    )
    step("A2 register/email", r.status_code in (201, 409), f"HTTP {r.status_code} (409=重跑已存在)")

    r = c.post("/api/v1/auth/login/email", json={"email": email, "password": "Test1234abc"})
    step("A2 login/email", r.status_code == 200, f"HTTP {r.status_code}")

    # 密码重置：独立手机号（首次发码无 60s 频率限制）-> reset -> 新密码登录
    phone3 = "13900000020"
    _ensure_account(c, phone3, tenant_name="smoke-A2r")
    r = c.post("/api/v1/auth/sms/send", json={"phone": phone3})
    code = r.json().get("code") if r.status_code == 200 else None
    r = c.post(
        "/api/v1/auth/password/reset",
        json={"target": phone3, "code": code, "new_password": "NewPass1234abc", "code_type": "sms"},
    )
    step("A2 password/reset", r.status_code == 200, f"HTTP {r.status_code} body={r.text[:60]}")

    r = c.post("/api/v1/auth/login", json={"phone": phone3, "password": "NewPass1234abc"})
    step("A2 新密码登录验证reset", r.status_code == 200, f"HTTP {r.status_code}")


# ============ A3 微信 mock 登录 ============
def a3(c):
    from sqlalchemy import text as _text
    from app.crud import user as user_crud

    # 先建账号，再清理 wechat 绑定（phone 列为 AES 密文，须用 service 层查 user_id 再清）
    token, _ = _ensure_account(c, "13900000003", tenant_name="smoke-A3")
    db = SessionLocal()
    try:
        u = user_crud.get_by_phone(db, "13900000003")
        if u:
            db.execute(_text("UPDATE users SET wechat_openid=NULL WHERE id=:uid"), {"uid": u.id})
            db.commit()
    finally:
        db.close()
    H = {"Authorization": f"Bearer {token}"}

    r = c.get("/api/v1/auth/wechat/auth", params={"redirect_uri": "https://x.com/cb", "state": "st1"})
    step("A3 wechat/auth", r.status_code == 200 and "auth_url" in (r.json() if r.status_code == 200 else {}), f"HTTP {r.status_code}")

    code = "mockcodeA3"
    r = c.get("/api/v1/auth/wechat/callback", params={"code": code})
    b = r.json() if r.status_code == 200 else {}
    step("A3 wechat/callback 未绑定", r.status_code == 200 and b.get("need_bind") is True, f"HTTP {r.status_code} need_bind={b.get('need_bind')}")
    openid = b.get("openid")

    r = c.post("/api/v1/auth/wechat/bind", json={"openid": openid}, headers=H)
    step("A3 wechat/bind", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.get("/api/v1/auth/wechat/callback", params={"code": code})
    b = r.json() if r.status_code == 200 else {}
    step("A3 wechat/callback 已绑定", r.status_code == 200 and b.get("need_bind") is False, f"HTTP {r.status_code} need_bind={b.get('need_bind')}")


# ============ A4 计费链路（mock 支付）============
def a4(c):
    token, tid = _ensure_account(c, "13900000010", tenant_name="smoke-A4-7")
    H = {"Authorization": f"Bearer {token}"}

    r = c.get("/api/v1/billing/quota", headers=H)
    step("A4 quota", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.get("/api/v1/billing/usage", headers=H)
    step("A4 usage", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.get("/api/v1/billing/invoices", headers=H)
    step("A4 invoices", r.status_code == 200, f"HTTP {r.status_code}")

    # pay 不存在的账单 -> 400（验证端点可达 + service 校验）
    r = c.post("/api/v1/billing/pay/999999", headers=H)
    step("A4 pay不存在账单(400)", r.status_code == 400, f"HTTP {r.status_code}")

    # 支付宝异步回调端点可达（mock 模式验签直接 True）
    r = c.post(
        "/api/v1/billing/alipay/notify",
        data={"out_trade_no": "SMOKE999", "trade_status": "TRADE_SUCCESS"},
    )
    step("A4 alipay/notify 可达", r.status_code in (200, 400), f"HTTP {r.status_code} body={r.text[:40]}")


# ============ A5 退款审批（mock）============
def a5(c):
    token, _ = _ensure_account(c, "13900000010", tenant_name="smoke-A4-7")
    H = {"Authorization": f"Bearer {token}"}

    r = c.get("/api/v1/order/refunds/list", headers=H)
    step("A5 refunds/list", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.get("/api/v1/order/list", headers=H)
    step("A5 order/list", r.status_code == 200, f"HTTP {r.status_code}")

    # approve 不存在的退款 -> 400
    r = c.post("/api/v1/order/refunds/999999/approve", json={"decision": "approved"}, headers=H)
    step("A5 approve不存在(400)", r.status_code == 400, f"HTTP {r.status_code}")


# ============ A6 工单列表/详情 ============
def a6(c):
    token, _ = _ensure_account(c, "13900000010", tenant_name="smoke-A4-7")
    H = {"Authorization": f"Bearer {token}"}

    r = c.get("/api/v1/ticket/list", headers=H)
    step("A6 ticket/list", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.get("/api/v1/ticket/999999", headers=H)
    step("A6 ticket不存在(404)", r.status_code == 404, f"HTTP {r.status_code}")


# ============ A7 租户管理（widget 配置验证刚补的列）============
def a7(c):
    token, tid = _ensure_account(c, "13900000010", tenant_name="smoke-A4-7")
    H = {"Authorization": f"Bearer {token}"}

    # 放开 seat_limit（trial 默认 1，admin 已占席位）以测邀请闭环
    from sqlalchemy import text as _text

    db = SessionLocal()
    try:
        db.execute(_text("UPDATE tenants SET seat_limit=5, wizard_step='basic' WHERE id=:tid"), {"tid": tid})
        db.commit()
    finally:
        db.close()

    # 配置向导
    r = c.get("/api/v1/tenant/wizard", headers=H)
    step("A7 wizard状态", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.post("/api/v1/tenant/wizard/next", headers=H)
    step("A7 wizard/next", r.status_code in (200, 409), f"HTTP {r.status_code} (409=已done)")

    # Widget 品牌配置 —— 写入刚补的 widget_logo_url / widget_welcome_text 列
    r = c.post(
        "/api/v1/tenant/widget",
        json={"theme_color": "#ff6600", "logo_url": "https://cdn.test/logo.png", "welcome_text": "欢迎"},
        headers=H,
    )
    b = r.json() if r.status_code == 200 else {}
    step(
        "A7 widget配置(写logo_url+welcome_text)",
        r.status_code == 200 and b.get("logo_url") == "https://cdn.test/logo.png",
        f"HTTP {r.status_code} logo_url={b.get('logo_url')}",
    )

    r = c.get("/api/v1/tenant/widget/embed-code", headers=H)
    step("A7 widget/embed-code", r.status_code == 200, f"HTTP {r.status_code}")

    # 成员邀请闭环
    r = c.post("/api/v1/tenant/invite", json={"phone": "13900000011"}, headers=H)
    b = r.json() if r.status_code == 200 else {}
    step("A7 invite", r.status_code == 200, f"HTTP {r.status_code}")
    invite_token = b.get("invite_token")

    if invite_token:
        r = c.post(
            "/api/v1/tenant/invite/accept",
            json={"token": invite_token, "phone": "13900000011", "password": "Test1234abc"},
        )
        step("A7 invite/accept", r.status_code in (200, 410), f"HTTP {r.status_code} (410=重跑已加入)")

    r = c.get("/api/v1/tenant/audit-logs", headers=H)
    step("A7 audit-logs", r.status_code == 200, f"HTTP {r.status_code}")


# ============ A8 会话生命周期（send_message 依赖 LLM，key 未填时容忍 500）============
def a8(c):
    token, tid = _ensure_account(c, "13900000040", tenant_name="smoke-A8")
    H = {"Authorization": f"Bearer {token}"}

    r = c.post("/api/v1/session", json={"end_user_id": "smoke-user-8"}, headers=H)
    b = r.json() if r.status_code == 201 else {}
    step("A8 create_session", r.status_code == 201, f"HTTP {r.status_code}")
    sid = b.get("session_id") or b.get("id")

    r = c.get("/api/v1/session/list", headers=H)
    step("A8 session/list", r.status_code == 200, f"HTTP {r.status_code}")

    # send_message 走 graph.invoke -> llm_complete -> litellm（DEEPSEEK_API_KEY 未填，可能 500）
    r = c.post("/api/v1/session/message", json={"session_id": sid, "content": "你好"}, headers=H)
    step("A8 send_message", r.status_code == 200, f"HTTP {r.status_code} (LLM key 未填可能失败) body={r.text[:50]}")

    r = c.get(f"/api/v1/session/{sid}", headers=H)
    step("A8 session detail", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.post(f"/api/v1/session/{sid}/claim", headers=H)
    step("A8 claim", r.status_code == 200, f"HTTP {r.status_code}")

    r = c.post(f"/api/v1/session/{sid}/return-to-agent", headers=H)
    step("A8 return-to-agent", r.status_code == 200, f"HTTP {r.status_code}")

    # leave-message 需 waiting_human + 无在线坐席；当前状态不符 -> 403/409 均算端点可达
    r = c.post(f"/api/v1/session/{sid}/leave-message", json={"content": "留言测试"}, headers=H)
    step("A8 leave-message 可达", r.status_code in (200, 403, 409), f"HTTP {r.status_code}")

    # csat 需 closed；未关闭 -> 403 算端点可达
    r = c.post(f"/api/v1/session/{sid}/csat", json={"score": 5, "comment": "good"}, headers=H)
    step("A8 csat 可达", r.status_code in (201, 403, 409), f"HTTP {r.status_code}")

    # feedback 用不存在 message_id -> 404 算端点可达
    r = c.post("/api/v1/session/message/999999/feedback", json={"score": 1, "comment": "useful"}, headers=H)
    step("A8 feedback 可达", r.status_code in (201, 404), f"HTTP {r.status_code}")

    # trace 走 MongoDB，未起时可能 500
    r = c.get(f"/api/v1/session/{sid}/trace", headers=H)
    step("A8 trace", r.status_code == 200, f"HTTP {r.status_code} (MongoDB 未起可能 500)")


# ============ A9 MCP 工具（mock，不依赖 LLM）============
def a9(c):
    token, _ = _ensure_account(c, "13900000050", tenant_name="smoke-A9")
    H = {"Authorization": f"Bearer {token}"}

    r = c.get("/api/v1/mcp/tools/list", headers=H)
    b = r.json() if r.status_code == 200 else {}
    tools = (b.get("result") or {}).get("tools", []) if r.status_code == 200 else []
    step("A9 tools/list", r.status_code == 200 and len(tools) > 0, f"HTTP {r.status_code} tools={len(tools)}")

    if tools:
        name = tools[0].get("name", "")
        r = c.post("/api/v1/mcp/tools/call", json={"name": name, "arguments": {}}, headers=H)
        step("A9 tools/call", r.status_code == 200, f"HTTP {r.status_code} name={name}")
    else:
        step("A9 tools/call", False, "无工具可调")


# ============ A10 Deep Agent（planner 可能依赖 LLM）============
def a10(c):
    token, _ = _ensure_account(c, "13900000060", tenant_name="smoke-A10")
    H = {"Authorization": f"Bearer {token}"}

    r = c.post("/api/v1/agent/deep-task", json={"goal": "生成本周工单积压分析报告"}, headers=H)
    b = r.json() if r.status_code == 200 else {}
    step("A10 deep-task 创建", r.status_code == 200, f"HTTP {r.status_code} (planner 可能依赖 LLM) body={r.text[:50]}")
    task_id = b.get("task_id")

    if task_id:
        r = c.get(f"/api/v1/agent/deep-task/{task_id}", headers=H)
        step("A10 deep-task 查询", r.status_code == 200, f"HTTP {r.status_code}")
    else:
        step("A10 deep-task 查询", False, "无 task_id")


# ============ B1 知识库全链路（真实 Milvus+ES+RabbitMQ+硅基流动 embedding）============
def b1(c):
    token, tid = _ensure_account(c, "13900000071", tenant_name="smoke-B1")
    H = {"Authorization": f"Bearer {token}"}

    # 上传 .md 知识库文档（trial 套餐上限 1 篇，重跑会 403 -> 复用已有文档）
    # 注意 _chunk_markdown 按 ## 标题分段，内容须含 ## 标题否则 chunks 为空 -> failed
    content = (
        "## 产品介绍\n"
        "智枢ZhiHub是企业级智能客服平台，支持知识库管理、多租户隔离、AI对话编排。\n\n"
        "## 知识库功能\n"
        "知识库支持上传PDF、Word、TXT文档，自动切片向量化，提供向量与BM25混合检索。\n\n"
        "## 工单系统\n"
        "工单系统支持退款审批闭环，管理员可审批或拒绝退款申请。\n\n"
        "## 安全隔离\n"
        "多租户隔离，tenant_id从JWT注入，绝不信任客户端传入。"
    ).encode("utf-8")
    r = c.post(
        "/api/v1/knowledge/upload",
        files={"file": ("smoke_kb.md", content, "text/markdown")},
        headers=H,
    )
    b = r.json() if r.status_code == 201 else {}
    doc_id = b.get("doc_id")
    step("B1 upload", r.status_code in (201, 403), f"HTTP {r.status_code} doc_id={doc_id} (403=trial上限重跑复用)")

    if not doc_id:
        # 超限或重跑 -> 从 list 取已有文档
        r = c.get("/api/v1/knowledge/list", headers=H)
        items = r.json().get("items", []) if r.status_code == 200 else []
        if items:
            doc_id = items[0].get("doc_id")
            step("B1 复用已有文档", True, f"doc_id={doc_id}")
        else:
            step("B1 无文档可测", False, "upload 失败且无已有文档")
            return

    # 轮询异步处理至终态（embedding + 双写，最多 40s）
    status = "pending"
    for _ in range(40):
        r = c.get("/api/v1/knowledge/list", headers=H)
        items = r.json().get("items", []) if r.status_code == 200 else []
        for it in items:
            if it.get("doc_id") == doc_id:
                status = it.get("status")
                break
        if status in ("ready", "failed", "pending_review"):
            break
        time.sleep(1)
    step("B1 异步处理 ready", status == "ready", f"status={status}")

    # 检索测试（向量 + 混合）
    r = c.post(
        "/api/v1/knowledge/test",
        json={"query": "知识库支持哪些文档格式", "history": []},
        headers=H,
    )
    b = r.json() if r.status_code == 200 else {}
    vec_n = len(b.get("vector_results", []))
    hyb_n = len(b.get("hybrid_results", []))
    step(
        "B1 test 检索",
        r.status_code == 200 and (vec_n > 0 or hyb_n > 0),
        f"HTTP {r.status_code} vector={vec_n} hybrid={hyb_n} empty={b.get('empty_knowledge')}",
    )

    # rebuild（202 Accepted 异步重建）
    r = c.post(f"/api/v1/knowledge/{doc_id}/rebuild", headers=H)
    step("B1 rebuild", r.status_code == 202, f"HTTP {r.status_code}")


def run() -> None:
    with TestClient(app) as c:
        for fn in (a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, b1):
            try:
                fn(c)
            except Exception as e:
                step(fn.__name__, False, f"EXC {type(e).__name__}: {e}")


if __name__ == "__main__":
    run()
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n=== {passed}/{total} passed ===")
    if failed:
        print("FAILED:", ", ".join(failed))
    sys.exit(0 if passed == total else 1)
