"""契约测试： 收尾修复（CORS 去重 / 品牌闭环 / Origin 信任顺序 / 嵌入配置校验）。"""

import pytest

from app.crud import tenant as tenant_crud
from app.models.tenant import Tenant


def _mk_tenant(db, *, domains="example.com"):
    from app.services import tenant_service
    tenant = tenant_crud.create_tenant(db, name="p021-tenant", plan_type="basic")
    db.commit()
    key = tenant_service.generate_widget_api_key(db, tenant.id)["api_key"]
    tenant_service.set_widget_allowed_domains(db, tenant.id, domains.split(","))
    return tenant, key


def _admin_headers(make_token, tenant_id):
    return {"Authorization": f"Bearer {make_token(tenant_id=tenant_id, role='admin')}"}


def _acao_values(resp):
    """从原始响应头元组中取出全部 Access-Control-Allow-Origin 值（bytes -> str）。"""
    return [
        value.decode("latin-1") if isinstance(value, bytes) else value
        for key, value in resp.headers.raw
        if isinstance(key, bytes) and key.decode("latin-1").lower() == "access-control-allow-origin"
    ]


def _vary_values(resp):
    out = []
    for key, value in resp.headers.raw:
        if isinstance(key, bytes) and key.decode("latin-1").lower() == "vary":
            out.extend(p.strip().lower() for p in value.decode("latin-1").split(",") if p.strip())
    return out


def _has_header(resp, name: str) -> bool:
    return any(
        isinstance(key, bytes) and key.decode("latin-1").lower() == name.lower()
        for key, _ in resp.headers.raw
    )


# ═══════════════════════════════════════════════════════════
# 一、CORS 响应头去重
# ═══════════════════════════════════════════════════════════
class TestCorsHeaderDedupe:
    """Origin 同时命中全局 CORS 与 Widget 动态白名单时，ACAO 必须只有一条。"""

    def test_single_acao_when_origin_in_both_lists(self, client, db):
        """白名单含 localhost；Origin=http://localhost:5173 同时在全局 cors_origins。"""
        tenant, key = _mk_tenant(db, domains="localhost")
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
            headers={"Origin": "http://localhost:5173"},
        )
        assert resp.status_code == 201, resp.text
        acao = _acao_values(resp)
        assert len(acao) == 1, f"ACAO 应只有一条，实际 {acao}"
        assert acao[0] == "http://localhost:5173"
        # Vary 中 Origin 只出现一次（全局 CORS 已加过）
        varies = _vary_values(resp)
        assert varies.count("origin") == 1, f"Vary 应只含一个 Origin，实际 {varies}"

    def test_preflight_single_acao(self, client, db):
        tenant, _ = _mk_tenant(db, domains="localhost")
        resp = client.options(
            "/api/v1/widget/session",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code == 204, resp.status_code
        acao = _acao_values(resp)
        assert len(acao) == 1, f"预检 ACAO 应只有一条，实际 {acao}"
        assert acao[0] == "http://localhost:5173"

    def test_unknown_origin_still_gets_no_cors_headers(self, client, db):
        tenant, key = _mk_tenant(db, domains="localhost")
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://evil.org"},
            headers={"Origin": "https://evil.org"},
        )
        # 业务层 403，且不给 CORS 放行头
        assert resp.status_code == 403
        assert _acao_values(resp) == []


class TestCorsNoCredentials:
    """: Widget 动态 CORS 必须显式删除全局 CORS 遗留的 Allow-Credentials。"""

    def test_actual_response_has_no_allow_credentials(self, client, db):
        tenant, key = _mk_tenant(db, domains="localhost")
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
            headers={"Origin": "http://localhost:5173"},  # 命中全局 CORS（allow_credentials=True）
        )
        assert resp.status_code == 201, resp.text
        assert len(_acao_values(resp)) == 1
        assert not _has_header(resp, "access-control-allow-credentials")

    def test_preflight_has_no_allow_credentials(self, client, db):
        tenant, _ = _mk_tenant(db, domains="localhost")
        resp = client.options(
            "/api/v1/widget/session",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code == 204
        assert len(_acao_values(resp)) == 1
        assert not _has_header(resp, "access-control-allow-credentials")

    def test_admin_api_global_cors_credentials_unchanged(self, client, db, make_token):
        """管理后台普通 API（/tenant/widget 配置查询，登录态）的全局 CORS 行为保持不变。"""
        tenant, _ = _mk_tenant(db)
        token = make_token(tenant_id=tenant.id, role="admin")
        resp = client.get(
            "/api/v1/tenant/widget",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:5173"},
        )
        assert resp.status_code == 200, resp.text
        assert _has_header(resp, "access-control-allow-credentials")


# ═══════════════════════════════════════════════════════════
# 二、品牌配置客户侧闭环（后端契约）
# ═══════════════════════════════════════════════════════════
class TestBrandInSessionResponse:
    def test_session_response_returns_theme_logo_welcome(self, client, db, make_token):
        tenant, key = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        r = client.post(
            "/api/v1/tenant/widget",
            json={
                "theme_color": "#123456",
                "logo_url": "https://cdn.example.com/logo.png",
                "welcome_text": "欢迎光临",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["theme_color"] == "#123456"
        assert body["logo_url"] == "https://cdn.example.com/logo.png"
        assert body["welcome_text"] == "欢迎光临"

    def test_logo_url_rejects_non_http_absolute(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        for bad in ("javascript:alert(1)", "ftp://files.example.com/x.png", "/relative/logo.png", "data:image/png;base64,xx"):
            r = client.post("/api/v1/tenant/widget", json={"logo_url": bad}, headers=headers)
            assert r.status_code == 422, f"logo_url={bad} 应 422，实际 {r.status_code}"

    def test_theme_color_rejects_non_hex(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        for bad in ("red", "#fff", "#12345g", "#1234567", "123456", '"><script>'):
            r = client.post("/api/v1/tenant/widget", json={"theme_color": bad}, headers=headers)
            assert r.status_code == 422, f"theme_color={bad} 应 422，实际 {r.status_code}"

    def test_theme_color_accepts_standard_hex(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        r = client.post("/api/v1/tenant/widget", json={"theme_color": "#1890ff"}, headers=headers)
        assert r.status_code == 200, r.text

    def test_legacy_bad_values_sanitized_in_session_response(self, client, db):
        """历史脏数据（绕过校验直写 DB）不得原样下发到客户侧。"""
        tenant, key = _mk_tenant(db)
        tenant.widget_logo_url = "javascript:alert(1)"
        tenant.widget_theme_color = "red"
        tenant.widget_welcome_text = "旧欢迎语"
        db.commit()
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["logo_url"] is None
        assert body["theme_color"] == "#1890ff"  # 回退默认
        assert body["welcome_text"] == "旧欢迎语"

    def test_logo_clear_with_empty_string(self, client, db, make_token):
        """: 明确提交空值 = 清除旧 Logo；None = 保持原值。"""
        tenant, key = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        # 先保存 Logo
        r = client.post(
            "/api/v1/tenant/widget",
            json={"logo_url": "https://cdn.example.com/logo.png"},
            headers=headers,
        )
        assert r.status_code == 200 and r.json()["logo_url"] == "https://cdn.example.com/logo.png"
        # 明确提交空串 → 清除
        r = client.post("/api/v1/tenant/widget", json={"logo_url": ""}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["logo_url"] is None
        # GET 配置无 Logo
        cfg = client.get("/api/v1/tenant/widget", headers=headers).json()
        assert cfg["logo_url"] is None
        # 新建 Widget 会话 logo_url 为 null
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://example.com"},
        )
        assert resp.status_code == 201
        assert resp.json()["logo_url"] is None

    def test_logo_absent_field_keeps_original(self, client, db, make_token):
        """不提交 logo_url 字段 = 保持原值（区别于空串清除）。"""
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        client.post("/api/v1/tenant/widget", json={"logo_url": "https://cdn.example.com/logo.png"}, headers=headers)
        r = client.post("/api/v1/tenant/widget", json={"theme_color": "#00ff00"}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["logo_url"] == "https://cdn.example.com/logo.png"


class TestStrictLogoUrl:
    """: 共享严格 Logo URL 规则（写入校验与读取清洗同一套口径）。"""

    BAD_URLS = (
        "https://",                                    # 无 hostname
        "https://user:pass@example.com/logo.png",      # 凭据
        "https://example.com/logo.png?x=1",            # query
        "https://example.com/logo.png#x",              # fragment
        "https://[broken",                             # 畸形 host
        "javascript:alert(1)",                         # 非法 scheme
        "https://example.com:99999/logo.png",          # 端口越界
        "https://exa\nmple.com/logo.png",              # 控制字符
    )

    def test_write_validation_rejects_all_bad_urls(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        for bad in self.BAD_URLS:
            r = client.post("/api/v1/tenant/widget", json={"logo_url": bad}, headers=headers)
            assert r.status_code == 422, f"logo_url={bad!r} 应 422，实际 {r.status_code}"

    def test_sanitize_uses_same_rules_for_legacy_dirty_data(self, client, db):
        from app.services.widget_service import sanitize_logo_url
        for bad in self.BAD_URLS:
            assert sanitize_logo_url(bad) is None, f"{bad!r} 应清洗为 None"

    def test_valid_absolute_url_passes(self, client, db, make_token):
        from app.services.widget_service import sanitize_logo_url
        assert sanitize_logo_url("https://cdn.example.com/logo.png") == "https://cdn.example.com/logo.png"
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        r = client.post(
            "/api/v1/tenant/widget",
            json={"logo_url": "https://cdn.example.com/logo.png"},
            headers=headers,
        )
        assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════
# 五、Origin 信任顺序
# ═══════════════════════════════════════════════════════════
class TestOriginTrustOrder:
    def test_header_origin_wins_over_body_origin(self, client, db):
        tenant, key = _mk_tenant(db, domains="shop.example.com")
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://evil.org"},
            headers={"Origin": "https://shop.example.com"},
        )
        assert resp.status_code == 201, resp.text

    def test_body_whitelisted_header_evil_rejected(self, client, db):
        tenant, key = _mk_tenant(db, domains="shop.example.com")
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
            headers={"Origin": "https://evil.org"},
        )
        assert resp.status_code == 403

    def test_referer_used_when_no_origin_header(self, client, db):
        tenant, key = _mk_tenant(db, domains="shop.example.com")
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://evil.org"},
            headers={"Referer": "https://shop.example.com/page"},
        )
        assert resp.status_code == 201, resp.text

    def test_body_fallback_for_non_browser_clients(self, client, db):
        tenant, key = _mk_tenant(db, domains="shop.example.com")
        resp = client.post(
            "/api/v1/widget/session",
            json={"api_key": key, "origin": "https://shop.example.com"},
        )
        assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════
# 六、嵌入配置校验
# ═══════════════════════════════════════════════════════════
class TestPublicBaseUrlValidation:
    def test_invalid_schemes_rejected(self, client, db, make_token, monkeypatch):
        from app.config import settings
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        for bad in (
            "javascript:alert(1)",
            "ftp://files.example.com",
            "https://user:pass@chat.example.com",
            "https://chat.example.com/base?q=1",
            "https://chat.example.com/#frag",
            "not a url",
            "https://",
            "chat.example.com",
        ):
            monkeypatch.setattr(settings, "public_base_url", bad)
            r = client.get("/api/v1/tenant/widget/embed-code", headers=headers)
            assert r.status_code == 409, f"PUBLIC_BASE_URL={bad} 应 409，实际 {r.status_code}"
            assert "PUBLIC_BASE_URL" in r.json()["detail"]

    def test_malformed_values_never_500(self, client, db, make_token, monkeypatch):
        """: 畸形值（坏 host/非法端口/控制字符）解析异常必须转 409 而非 500。"""
        from app.config import settings
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        for bad in (
            "https://[broken",                 # 坏 IPv6 字面量
            "https://example.com:99999/",      # 端口越界
            "https://exa\nmple.com",           # 控制字符
            "https://\x00example.com",         # NUL
            "https://example.com:abc/",        # 非数字端口
        ):
            monkeypatch.setattr(settings, "public_base_url", bad)
            r = client.get("/api/v1/tenant/widget/embed-code", headers=headers)
            assert r.status_code == 409, f"PUBLIC_BASE_URL={bad!r} 应 409，实际 {r.status_code}"
            assert "PUBLIC_BASE_URL" in r.json()["detail"]

    def test_trailing_slash_normalized(self, client, db, make_token, monkeypatch):
        from app.config import settings
        tenant, _ = _mk_tenant(db)
        monkeypatch.setattr(settings, "public_base_url", "https://chat.example.com/")
        r = client.get(
            "/api/v1/tenant/widget/embed-code",
            headers=_admin_headers(make_token, tenant.id),
        )
        assert r.status_code == 200, r.text
        code = r.json()["embed_code"]
        assert 'src="https://chat.example.com/widget.js"' in code
        assert "//widget.js" not in code

    def test_embed_code_escapes_attribute_values(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        # 绕过校验直写恶意主题色，验证嵌入代码属性转义
        tenant.widget_theme_color = '"><script>alert(1)</script>'
        db.commit()
        r = client.get(
            "/api/v1/tenant/widget/embed-code",
            headers=_admin_headers(make_token, tenant.id),
        )
        assert r.status_code == 200, r.text
        code = r.json()["embed_code"]
        assert '<script>alert(1)</script>' not in code
        assert '&quot;&gt;&lt;script&gt;' in code or '&quot;' in code


class TestDomainWhitelistValidation:
    def test_scheme_path_query_rejected(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        r = client.put(
            "/api/v1/tenant/widget/domains",
            json={"domains": ["https://shop.example.com"]},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        for bad in (["shop.example.com/path"], ["shop.example.com?q=1"], ["shop.example.com#f"], ["shop.example.com:8080"]):
            r = client.put("/api/v1/tenant/widget/domains", json={"domains": bad}, headers=headers)
            assert r.status_code == 422, f"domains={bad} 应 422，实际 {r.status_code}"

    def test_valid_hostname_ip_localhost_accepted(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        r = client.put(
            "/api/v1/tenant/widget/domains",
            json={"domains": ["shop.example.com", "127.0.0.1", "localhost"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert sorted(r.json()["domains"]) == ["127.0.0.1", "localhost", "shop.example.com"]

    def test_service_layer_rejects_invalid_entries(self, db):
        from app.services import tenant_service
        tenant, _ = _mk_tenant(db)
        with pytest.raises(tenant_service.TenantError):
            tenant_service.set_widget_allowed_domains(db, tenant.id, ["https://evil.com"])
        with pytest.raises(tenant_service.TenantError):
            tenant_service.set_widget_allowed_domains(db, tenant.id, ["a.com/path"])

    def test_asterisk_prefix_exact_match_only(self, client, db, make_token):
        """: 仅精确 '*.' 前缀被移除；'**.' 或 '....' 开头必须拒绝。"""
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        for bad in (["**example.com"], ["....example.com"], ["***.example.com"]):
            r = client.put("/api/v1/tenant/widget/domains", json={"domains": bad}, headers=headers)
            assert r.status_code == 422, f"domains={bad} 应 422，实际 {r.status_code}"

    def test_domains_deduplicated(self, client, db, make_token):
        tenant, _ = _mk_tenant(db)
        headers = _admin_headers(make_token, tenant.id)
        r = client.put(
            "/api/v1/tenant/widget/domains",
            json={"domains": ["a.example.com", "A.EXAMPLE.COM", "a.example.com", "b.example.com"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["domains"] == ["a.example.com", "b.example.com"]
