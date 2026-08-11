"""
backend/tests/integration/test_auth_middleware.py

职责: 测试 auth/tenant 中间件集成（令牌校验、公开路径放行、黑名单拒绝、伪造 tenant_id 忽略）
对应日程: 001-auth-tenant

测试内容:
  - TestAuthMiddleware: 有效令牌通过 / 无令牌 401 / 畸形令牌 401 /
    公开路径（health/register/login）免鉴权 / 登出后黑名单 401
  - TestTenantMiddleware: SC-006 令牌 tenant=1，请求自报 tenant_id=999 被忽略
"""


HOME = "/api/v1/tenant/home"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_login(client, phone="13800138000"):
    code = client.post("/api/v1/auth/sms/send", json={"phone": phone}).json()["code"]
    client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "abc12345", "tenant_name": "示例科技", "code": code},
    )
    return client.post(
        "/api/v1/auth/login", json={"phone": phone, "password": "abc12345"}
    ).json()["access_token"]


class TestAuthMiddleware:
    def test_valid_token_passes(self, client):
        token = _register_login(client)
        assert client.get(HOME, headers=_auth(token)).status_code == 200

    def test_no_token_rejected(self, client):
        assert client.get(HOME).status_code == 401

    def test_malformed_token_rejected(self, client):
        assert client.get(HOME, headers=_auth("not.a.jwt")).status_code == 401

    def test_public_paths_bypass_auth(self, client):
        # health 与 register/login 免鉴权
        assert client.get("/health").status_code == 200
        code = client.post("/api/v1/auth/sms/send", json={"phone": "13811112222"}).json()["code"]
        assert client.post(
            "/api/v1/auth/register",
            json={"phone": "13811112222", "password": "abc12345", "tenant_name": "X", "code": code},
        ).status_code == 201

    def test_blocklisted_token_rejected(self, client):
        token = _register_login(client)
        client.post("/api/v1/auth/logout", headers=_auth(token))
        assert client.get(HOME, headers=_auth(token)).status_code == 401


class TestTenantMiddleware:
    def test_client_tenant_id_ignored(self, client):
        """SC-006: 令牌 tenant=1，请求 query 自报 tenant_id=999，实际生效仍为 1。"""
        token = _register_login(client)
        real_tid = client.get(HOME, headers=_auth(token)).json()["tenant_id"]
        # 携带伪造 query 参数
        spoofed = client.get(f"{HOME}?tenant_id=999", headers=_auth(token))
        assert spoofed.status_code == 200
        assert spoofed.json()["tenant_id"] == real_tid
