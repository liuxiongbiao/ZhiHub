"""
backend/tests/integration/test_deps.py

职责: 测试 api/deps.py 依赖注入（get_current_user 加载用户、require_role 越权拦截）

测试内容:
  - TestRequireRole: agent 角色访问 admin 端点 403 / admin 角色通过
  - TestGetCurrentUser: 令牌指向不存在 user_id -> get_current_user 401
"""


BILLING = "/api/v1/tenant/billing"
HOME = "/api/v1/tenant/home"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, phone="13800138000"):
    code = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()["code"]
    return client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "abc12345", "tenant_name": "示例科技", "code": code},
    ).json()


class TestRequireRole:
    def test_agent_forbidden_on_admin_endpoint(self, client, make_token):
        _register(client)  # 建租户 1
        agent_token = make_token(user_id=99, tenant_id=1, role="agent")
        assert client.get(BILLING, headers=_auth(agent_token)).status_code == 403

    def test_admin_allowed_on_admin_endpoint(self, client):
        reg = _register(client)
        login = client.post(
            "/api/v1/auth/login", json={"phone": "13800138000", "password": "abc12345"}
        ).json()
        resp = client.get(BILLING, headers=_auth(login["access_token"]))
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == reg["tenant_id"]


class TestGetCurrentUser:
    def test_unknown_user_id_rejected(self, client, make_token):
        # 令牌指向不存在的 user_id → get_current_user 401
        ghost = make_token(user_id=99999, tenant_id=1, role="admin")
        # home 依赖 get_current_user
        assert client.get(HOME, headers=_auth(ghost)).status_code == 401
