"""
backend/tests/conftest.py

职责: 全局测试夹具，让 295 个测试脱离真实 MySQL/Redis 容器独立运行

  - db: SQLite in-memory Session。用 StaticPool 强制全测试共用同一连接，
        否则 :memory: 每个连接会建独立库（建表后查不到）。每测试建表 + 结束销毁。
  - fake_redis(autouse): 用 fakeredis 替换 core.redis 的连接单例。autouse 保证
        每个测试拿到干净的 Redis 状态（失败计数/黑名单/熔断计数不串测试）。
  - client: TestClient 覆盖 get_db 依赖指向内存库，中间件链路（auth/tenant）保持
        真实，从而能测到中间件层的行为而非仅路由层。
  - make_token: 令牌工厂 make_token(user_id, tenant_id, role) -> JWT。
        不在夹具里写死角色，由各测试按需传参（admin/agent/过期）。
"""

import os

# /: 测试环境统一为 test —— 验证码回显、邮箱激活行为与生产隔离
os.environ.setdefault("APP_ENV", "test")
# 测试禁止向真实 Langfuse 账号导出 trace（本机 .env 含真实 key）
os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "")
os.environ.setdefault("LANGFUSE_SECRET_KEY", "")

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import redis as redis_module
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.base import Base

# 触发所有 ORM 模型注册到 Base.metadata（建表需要）
from app.models import audit_log as _audit_log  # noqa: F401
from app.models import answer_feedback as _answer_feedback  # noqa: F401
from app.models import user as _user  # noqa: F401
from app.models import tenant as _tenant  # noqa: F401
from app.models import tenant_member as _tm  # noqa: F401
from app.models import knowledge_document as _kd  # noqa: F401
from app.models import message as _msg  # noqa: F401
from app.models import notification as _notification  # noqa: F401
from app.models import order as _order  # noqa: F401
from app.models import quick_reply as _quick_reply  # noqa: F401
from app.models import refund_approval as _ra  # noqa: F401
from app.models import refund_approval_event as _rae  # noqa: F401
from app.models import refund_outbox as _ro  # noqa: F401
from app.models import session as _sess  # noqa: F401
from app.models import billing_invoice as _bi  # noqa: F401
from app.models import csat as _csat  # noqa: F401
from app.models import ticket as _ticket  # noqa: F401
from app.models import usage_record as _ur  # noqa: F401


@pytest.fixture()
def db():
    """SQLite in-memory 会话。StaticPool 让全测试共用同一连接，否则 :memory: 每连接独立建库。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # 全测试共用同一连接，否则 :memory: 每连接一个独立库（建表后查不到）
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def fake_redis():
    """fakeredis 替换 core.redis 单例。autouse 保证每测试 Redis 状态隔离（失败计数/黑名单/熔断不串）。"""
    server = fakeredis.FakeServer()
    fake = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    redis_module._redis_client = fake
    yield fake
    redis_module._redis_client = None


@pytest.fixture(autouse=True)
def fake_analytics_store(monkeypatch):
    """单元/契约测试禁止向本机真实 Mongo 写运营事件。"""
    from app.services import analytics_service

    class _TestCollection:
        def __init__(self):
            self.documents = []

        def create_index(self, *_args, **_kwargs):
            return "test-index"

        def insert_one(self, document):
            self.documents.append(document)
            return object()

    collection = _TestCollection()
    monkeypatch.setattr(analytics_service, "_collection", collection)
    monkeypatch.setattr(analytics_service, "_indexes_ready", False)
    yield collection


@pytest.fixture(autouse=True)
def reset_third_party_clients(monkeypatch):
    """测试环境与本地 .env 解耦：支付宝/微信一律 mock，避免真实 Key 影响断言。

    修复：契约测试此前依赖本机 .env 中 ALIPAY_APP_ID 是否为空，
    导致“真实模式密钥解析失败 -> 拒绝退款”的不稳定失败。
    """
    from app.config import settings
    from app.core.alipay_client import reset_alipay_client
    from app.core.wechat_client import reset_wechat_client
    from app.core.ocr_client import reset_ocr_client

    monkeypatch.setattr(settings, "alipay_app_id", "")
    monkeypatch.setattr(settings, "wechat_app_id", "")
    monkeypatch.setattr(settings, "ocr_api_key", "")
    monkeypatch.setattr(settings, "ocr_secret_key", "")
    reset_alipay_client()
    reset_wechat_client()
    reset_ocr_client()
    yield
    reset_alipay_client()
    reset_wechat_client()
    reset_ocr_client()


@pytest.fixture()
def client(db):
    """TestClient：覆盖 get_db 指向内存库，中间件链路（auth/tenant）保持真实，测到完整请求链。"""
    from app.main import app

    class _CSRFInjectingClient(TestClient):
        """自动为带 Authorization 的请求注入 X-CSRF-Token（对齐前端 request.ts 行为）。"""

        def request(self, method, url, **kwargs):
            headers = dict(kwargs.get("headers") or {})
            auth = headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                headers["X-CSRF-Token"] = auth[len("Bearer "):].strip()
                kwargs["headers"] = headers
            return super().request(method, url, **kwargs)

    def _override_get_db():
        try:
            yield db
        finally:
            pass  # 会话生命周期由 db 夹具管理

    app.dependency_overrides[get_db] = _override_get_db
    with _CSRFInjectingClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def make_token():
    """令牌工厂：不写死角色，由测试按需传参 make_token(user_id, tenant_id, role) → JWT。"""

    def _make(user_id: int = 1, tenant_id: int = 1, role: str = "admin", ws: bool = False) -> str:
        claims: dict = {"tenant_id": tenant_id, "role": role}
        if ws:
            claims["ws"] = True  # : 坐席工作台 WS 需短效令牌声明
        return create_access_token(subject=str(user_id), extra_claims=claims)

    return _make


def register_user_via_api(client, *, phone="13800138000", password="abc12345",
                          tenant_name="示例科技"):
    """HTTP 注册（含短信验证码，PRD 3.1.1）。返回 register 响应 json。"""
    code = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()["code"]
    return client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": password, "tenant_name": tenant_name, "code": code},
    ).json()
