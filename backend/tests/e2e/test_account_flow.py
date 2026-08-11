"""
tests/e2e/test_account_flow.py

端到端全链路：注册(含短信验证码)→登录→进后台首页；登出即时失效；坐席越权 403。
覆盖 spec SC-001/002/004/005，走真实中间件链路（TestClient）。
TDD: 先写并 FAIL，再实现路由与中间件。
"""


REGISTER = "/api/v1/auth/register"
SMS_SEND = "/api/v1/auth/sms/send"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
HOME = "/api/v1/tenant/home"
BILLING = "/api/v1/tenant/billing"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _code(client, phone):
    """获取短信验证码（demo 模式回传 code，PRD 3.1.1）。"""
    return client.post(SMS_SEND, json={"phone": phone}).json()["code"]


def _register(client, phone="13800138000", password="abc12345", tenant_name="示例科技"):
    """注册（含短信验证码），返回响应。"""
    return client.post(
        REGISTER,
        json={"phone": phone, "password": password, "tenant_name": tenant_name, "code": _code(client, phone)},
    )


# ============ US1 注册端点契约 ============
class TestRegisterEndpoint:
    def test_register_returns_201(self, client):
        resp = _register(client)
        assert resp.status_code == 201
        body = resp.json()
        assert body["phone"] == "13800138000"
        assert body["role"] == "admin"
        assert body["plan_type"] == "trial"

    def test_register_duplicate_returns_409(self, client, fake_redis):
        _register(client, phone="13800138000")
        # 清除短信频率限制后重新获取验证码注册同号 -> 409（手机号已存在）
        fake_redis.delete("vcode_freq:sms:13800138000")
        resp = _register(client, phone="13800138000")
        assert resp.status_code == 409

    def test_register_weak_password_returns_422(self, client):
        # 弱密码 + 有效验证码 -> 422
        resp = client.post(
            REGISTER,
            json={"phone": "13800138002", "password": "12345678", "tenant_name": "A", "code": _code(client, "13800138002")},
        )
        assert resp.status_code == 422


# ============ US2 注册→登录→进后台 ============
class TestRegisterLoginHome:
    def test_full_flow(self, client):
        _register(client)
        login = client.post(LOGIN, json={"phone": "13800138000", "password": "abc12345"})
        assert login.status_code == 200
        token = login.json()["access_token"]

        home = client.get(HOME, headers=_auth(token))
        assert home.status_code == 200
        body = home.json()
        assert body["tenant_name"] == "示例科技"
        assert body["plan_type"] == "trial"
        assert body["current_user"]["role"] == "admin"

    def test_home_without_token_401(self, client):
        assert client.get(HOME).status_code == 401


# ============ US3 登出即时失效 ============
class TestLogoutFlow:
    def _login_token(self, client):
        _register(client)
        return client.post(LOGIN, json={"phone": "13800138000", "password": "abc12345"}).json()["access_token"]

    def test_logout_blocklists_token(self, client):
        token = self._login_token(client)
        assert client.post(LOGOUT, headers=_auth(token)).status_code == 200
        assert client.get(HOME, headers=_auth(token)).status_code == 401


# ============ US4 越权拦截 ============
class TestRoleIsolation:
    def test_agent_token_forbidden_on_admin_endpoint(self, client, make_token):
        _register(client)
        agent_token = make_token(user_id=2, tenant_id=1, role="agent")
        assert client.get(BILLING, headers=_auth(agent_token)).status_code == 403

    def test_admin_token_allowed_on_admin_endpoint(self, client):
        _register(client)
        admin_token = client.post(
            LOGIN, json={"phone": "13800138000", "password": "abc12345"}
        ).json()["access_token"]
        assert client.get(BILLING, headers=_auth(admin_token)).status_code == 200
