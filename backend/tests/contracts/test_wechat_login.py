"""
backend/tests/contracts/test_wechat_login.py

职责: 测试 PRD 3.1 微信扫码登录的 mock 契约（key 占位，真实验证待用户填 key）
对应日程: PRD 3.1 #12

测试内容（mock 契约，monkeypatch wechat_client）:
  - TestWechatMockMode: key 为空时 client 自动走 mock 模式
  - TestWechatAuthUrl: get_auth_url 生成授权 URL（含 redirect_uri / state）
  - TestWechatLoginStart: wechat_login_start 返回授权 URL
  - TestWechatCallbackBound: 已绑定用户回调 -> 换 openid -> 登录返回 token
  - TestWechatCallbackUnbound: 未绑定用户回调 -> 返回 need_bind + openid
  - TestWechatBind: 已登录用户绑定 openid 成功
  - TestWechatBindConflict: openid 已绑定其他账号 -> 冲突拒绝
  - TestWechatEndpoints: HTTP 端点契约（/wechat/auth、/wechat/callback、/wechat/bind）

设计决策:
  - 用 monkeypatch 替换 wechat_client 单例方法，隔离真实微信网关
  - mock 模式下 exchange_code_for_openid 返回 mock_openid_{code}，本契约显式控制返回值以验证分支
  - /wechat/auth 与 /wechat/callback 公开免鉴权，/wechat/bind 需 JWT
"""

import pytest

from app.crud import user as user_crud


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════
def _register_user(db, phone="13800138000", password="abc12345"):
    """注册一个用户供微信绑定测试，返回 (user, tenant)。"""
    from app.services import auth_service

    result = auth_service.register(db, phone=phone, password=password, tenant_name="测试企业")
    return result


def _patch_wechat(monkeypatch, *, openid="wx_openid_test"):
    """monkeypatch wechat_client 单例方法，返回受控结果。

    exchange_code_for_openid 统一返回 openid（不区分 code 值），简化测试断言。
    """
    from app.core import wechat_client as wc

    monkeypatch.setattr(
        wc.WeChatClient,
        "get_auth_url",
        lambda self, redirect_uri, state: (
            f"https://open.weixin.qq.com/connect/oauth2/authorize"
            f"?mock=1&redirect_uri={redirect_uri}&state={state}"
        ),
    )
    monkeypatch.setattr(
        wc.WeChatClient,
        "exchange_code_for_openid",
        lambda self, code: openid,
    )


def _issue_state(fake_redis, state="st_valid"):
    """向 Redis 预置一次性 state（模拟 wechat_login_start 服务端签发，T12）。"""
    fake_redis.set(f"wechat:state:{state}", "1", ex=600)
    return state


# ═══════════════════════════════════════════════════════════
# T1: mock 模式（key 为空时自动降级）
# ═══════════════════════════════════════════════════════════
class TestWechatMockMode:
    """未配置 wechat_app_id 时 client 走 mock 模式（不调真实网关）。"""

    def test_mock_mode_when_app_id_empty(self, monkeypatch):
        from app.core.wechat_client import WeChatClient
        from app.config import settings

        monkeypatch.setattr(settings, "wechat_app_id", "")
        client = WeChatClient()
        assert client.is_mock is True

        url = client.get_auth_url("https://example.com/cb", "state123")
        assert "mock" in url  # T34: 原 or url.startswith("http") 对任何 URL 恒真

        openid = client.exchange_code_for_openid("abc123")
        assert openid == "mock_openid_abc123"

    def test_real_mode_when_app_id_set(self, monkeypatch):
        """配置了 app_id 即视为意图启用真实模式（is_mock=False）。"""
        from app.core.wechat_client import WeChatClient
        from app.config import settings

        monkeypatch.setattr(settings, "wechat_app_id", "wx1234567890")
        client = WeChatClient()
        assert client.is_mock is False


# ═══════════════════════════════════════════════════════════
# T2: get_auth_url 契约
# ═══════════════════════════════════════════════════════════
class TestWechatAuthUrl:
    """get_auth_url 生成授权 URL，包含 redirect_uri 和 state。"""

    def test_auth_url_contains_redirect_and_state(self, monkeypatch):
        from app.core.wechat_client import WeChatClient
        from app.config import settings

        monkeypatch.setattr(settings, "wechat_app_id", "")
        client = WeChatClient()

        url = client.get_auth_url("https://example.com/cb", "xyz789")
        assert "redirect_uri" in url
        assert "xyz789" in url

    def test_auth_url_mock_contains_mock_flag(self, monkeypatch):
        from app.core.wechat_client import WeChatClient
        from app.config import settings

        monkeypatch.setattr(settings, "wechat_app_id", "")
        client = WeChatClient()

        url = client.get_auth_url("https://example.com/cb", "state1")
        assert "mock" in url


# ═══════════════════════════════════════════════════════════
# T3: wechat_login_start 返回授权 URL
# ═══════════════════════════════════════════════════════════
class TestWechatLoginStart:
    """wechat_login_start 服务层：返回授权 URL 给前端跳转。"""

    def test_login_start_returns_auth_url_with_server_state(self, monkeypatch, fake_redis):
        """服务端签发 state 并内嵌 auth_url（T12: 不再信任客户端 state）。"""
        _patch_wechat(monkeypatch)
        from app.services import auth_service

        result = auth_service.wechat_login_start(redirect_uri="https://example.com/cb")
        assert "auth_url" in result
        assert "redirect_uri" in result["auth_url"]
        assert "state=" in result["auth_url"]
        # state 已存入 Redis（一次性、带 TTL）
        state = result["auth_url"].split("state=")[1]
        assert fake_redis.exists(f"wechat:state:{state}") == 1
        assert fake_redis.ttl(f"wechat:state:{state}") > 0


# ═══════════════════════════════════════════════════════════
# T4: 已绑定用户回调 -> 登录返回 token
# ═══════════════════════════════════════════════════════════
class TestWechatCallbackBound:
    """回调换 openid -> 查到绑定 -> 签发 JWT 登录。"""

    def test_bound_user_returns_token(self, db, monkeypatch, fake_redis):
        _patch_wechat(monkeypatch, openid="wx_bound_001")
        from app.services import auth_service

        # 注册用户并预绑 openid
        reg = _register_user(db)
        user = user_crud.get_by_id(db, reg.user_id)
        user.wechat_openid = "wx_bound_001"
        db.flush()

        result = auth_service.wechat_login_callback(db, code="any_code", state=_issue_state(fake_redis))

        assert result.need_bind is False
        assert result.access_token
        assert result.user_id == reg.user_id
        assert result.tenant_id == reg.tenant_id
        assert result.role == "admin"

    def test_bound_user_token_decodes(self, db, monkeypatch, fake_redis):
        """返回的 token 可被 decode_access_token 解码，sub=user_id。"""
        _patch_wechat(monkeypatch, openid="wx_bound_002")
        from app.core.security import decode_access_token
        from app.services import auth_service

        reg = _register_user(db, phone="13900139000")
        user = user_crud.get_by_id(db, reg.user_id)
        user.wechat_openid = "wx_bound_002"
        db.flush()

        result = auth_service.wechat_login_callback(db, code="code_xyz", state=_issue_state(fake_redis))
        payload = decode_access_token(result.access_token)
        assert payload is not None
        assert payload["sub"] == str(reg.user_id)


# ═══════════════════════════════════════════════════════════
# T5: 未绑定用户回调 -> 返回 need_bind + openid
# ═══════════════════════════════════════════════════════════
class TestWechatCallbackUnbound:
    """回调换 openid -> 查不到绑定 -> 返回 need_bind=True + openid。"""

    def test_unbound_returns_need_bind(self, db, monkeypatch, fake_redis):
        _patch_wechat(monkeypatch, openid="wx_unbound_001")
        from app.services import auth_service

        # 不预绑任何用户
        result = auth_service.wechat_login_callback(db, code="new_code", state=_issue_state(fake_redis))

        assert result.need_bind is True
        assert result.openid == "wx_unbound_001"
        assert result.access_token is None

    def test_unbound_does_not_create_user(self, db, monkeypatch, fake_redis):
        """未绑定时不应自动创建用户，需引导用户先注册/登录再绑定。"""
        _patch_wechat(monkeypatch, openid="wx_unbound_002")
        from app.services import auth_service

        auth_service.wechat_login_callback(db, code="code2", state=_issue_state(fake_redis))

        existing = user_crud.get_by_openid(db, "wx_unbound_002")
        assert existing is None


# ═══════════════════════════════════════════════════════════
# T6: 已登录用户绑定 openid
# ═══════════════════════════════════════════════════════════
class TestWechatBind:
    """wechat_bind：已登录用户将 openid 绑定到自己的账号。"""

    def test_bind_success(self, db, monkeypatch):
        _patch_wechat(monkeypatch, openid="wx_bind_001")
        from app.services import auth_service

        reg = _register_user(db)
        auth_service.wechat_bind(db, user_id=reg.user_id, code="wx_bind_001")

        user = user_crud.get_by_id(db, reg.user_id)
        assert user.wechat_openid == "wx_bind_001"

    def test_bind_then_login_via_wechat(self, db, monkeypatch, fake_redis):
        """绑定后，用同一 openid 走回调即可登录。"""
        _patch_wechat(monkeypatch, openid="wx_bind_002")
        from app.services import auth_service

        reg = _register_user(db, phone="13700137000")
        auth_service.wechat_bind(db, user_id=reg.user_id, code="wx_bind_002")

        result = auth_service.wechat_login_callback(db, code="any", state=_issue_state(fake_redis))
        assert result.need_bind is False
        assert result.user_id == reg.user_id


# ═══════════════════════════════════════════════════════════
# T7: openid 已绑定其他账号 -> 冲突
# ═══════════════════════════════════════════════════════════
class TestWechatBindConflict:
    """一个 openid 只能绑定一个账号，重复绑定 -> 冲突拒绝。"""

    def test_bind_conflict_different_user(self, db, monkeypatch):
        _patch_wechat(monkeypatch, openid="wx_conflict_001")
        from app.services import auth_service
        from app.services.auth_service import WechatBindConflict

        reg1 = _register_user(db, phone="13800138001")
        reg2 = _register_user(db, phone="13800138002")
        # reg1 先绑定
        auth_service.wechat_bind(db, user_id=reg1.user_id, code="wx_conflict_001")

        # reg2 试图绑定同一 openid -> 冲突
        with pytest.raises(WechatBindConflict):
            auth_service.wechat_bind(db, user_id=reg2.user_id, code="wx_conflict_001")

    def test_bind_same_user_idempotent(self, db, monkeypatch):
        """同一用户重复绑定同一 openid 不报错（幂等）。"""
        _patch_wechat(monkeypatch, openid="wx_idem_001")
        from app.services import auth_service

        reg = _register_user(db, phone="13800138003")
        auth_service.wechat_bind(db, user_id=reg.user_id, code="wx_idem_001")
        # 再绑一次不抛异常
        auth_service.wechat_bind(db, user_id=reg.user_id, code="wx_idem_001")


# ═══════════════════════════════════════════════════════════
# T8: HTTP 端点契约
# ═══════════════════════════════════════════════════════════
class TestWechatEndpoints:
    """HTTP 端点契约：/wechat/auth、/wechat/callback、/wechat/bind。"""

    def test_auth_endpoint_public(self, client, monkeypatch):
        """GET /wechat/auth 公开免鉴权，返回授权 URL。"""
        _patch_wechat(monkeypatch)
        resp = client.get(
            "/api/v1/auth/wechat/auth",
            params={"redirect_uri": "https://example.com/cb"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "auth_url" in body
        assert "state=" in body["auth_url"]

    def test_callback_endpoint_bound_login(self, client, db, monkeypatch, fake_redis):
        """GET /wechat/callback 回调，已绑定 -> 返回 token。"""
        _patch_wechat(monkeypatch, openid="wx_http_bound")
        from app.services import auth_service

        reg = _register_user(db)
        user = user_crud.get_by_id(db, reg.user_id)
        user.wechat_openid = "wx_http_bound"
        db.commit()

        resp = client.get(
            "/api/v1/auth/wechat/callback",
            params={"code": "http_code", "state": _issue_state(fake_redis)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["need_bind"] is False
        assert body["access_token"]
        assert body["user_id"] == reg.user_id

    def test_callback_endpoint_unbound_need_bind(self, client, monkeypatch, fake_redis):
        """GET /wechat/callback 回调，未绑定 -> 返回 need_bind + openid。"""
        _patch_wechat(monkeypatch, openid="wx_http_unbound")
        resp = client.get(
            "/api/v1/auth/wechat/callback",
            params={"code": "http_code2", "state": _issue_state(fake_redis)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["need_bind"] is True
        assert body["openid"] == "wx_http_unbound"

    def test_bind_endpoint_requires_auth(self, client, monkeypatch):
        """POST /wechat/bind 无 JWT -> 401。"""
        _patch_wechat(monkeypatch)
        resp = client.post(
            "/api/v1/auth/wechat/bind",
            json={"code": "wx_no_auth"},
        )
        assert resp.status_code == 401

    def test_bind_endpoint_success(self, client, db, make_token, monkeypatch):
        """POST /wechat/bind 已登录用户绑定 openid -> 200。"""
        _patch_wechat(monkeypatch, openid="wx_http_bind")
        from app.services import auth_service

        reg = _register_user(db)
        db.commit()
        token = make_token(user_id=reg.user_id, tenant_id=reg.tenant_id, role="admin")

        resp = client.post(
            "/api/v1/auth/wechat/bind",
            json={"code": "wx_http_bind"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "openid" in body

    def test_bind_endpoint_conflict(self, client, db, make_token, monkeypatch):
        """POST /wechat/bind openid 已被他人绑定 -> 409。"""
        _patch_wechat(monkeypatch, openid="wx_http_conflict")
        from app.services import auth_service

        reg1 = _register_user(db, phone="13800138010")
        reg2 = _register_user(db, phone="13800138011")
        # reg1 先绑
        user1 = user_crud.get_by_id(db, reg1.user_id)
        user1.wechat_openid = "wx_http_conflict"
        db.commit()

        token2 = make_token(user_id=reg2.user_id, tenant_id=reg2.tenant_id, role="admin")
        resp = client.post(
            "/api/v1/auth/wechat/bind",
            json={"code": "wx_http_conflict"},
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert resp.status_code == 409


# ═══════════════════════════════════════════════════════════
# T12: state 校验（OAuth CSRF 防护）
# ═══════════════════════════════════════════════════════════
class TestWechatState:
    """回调 state 必须由 wechat_login_start 签发且一次性使用。"""

    def test_callback_missing_state_rejected(self, db, monkeypatch):
        """无 state 参数 -> 拒绝（WechatStateError）。"""
        _patch_wechat(monkeypatch)
        from app.services import auth_service
        from app.services.auth_service import WechatStateError

        with pytest.raises(WechatStateError):
            auth_service.wechat_login_callback(db, code="any")

    def test_callback_unissued_state_rejected(self, db, monkeypatch, fake_redis):
        """state 未签发（Redis 无记录）-> 拒绝。"""
        _patch_wechat(monkeypatch)
        from app.services import auth_service
        from app.services.auth_service import WechatStateError

        with pytest.raises(WechatStateError):
            auth_service.wechat_login_callback(db, code="any", state="forged_state")

    def test_state_single_use(self, db, monkeypatch, fake_redis):
        """state 一次性: 用过一次后再次使用 -> 拒绝。"""
        _patch_wechat(monkeypatch)
        from app.services import auth_service
        from app.services.auth_service import WechatStateError

        state = _issue_state(fake_redis, state="st_once")
        # 第一次消费成功
        result = auth_service.wechat_login_callback(db, code="code1", state=state)
        assert result.need_bind is True
        # 第二次（同一 state）-> 拒绝
        with pytest.raises(WechatStateError):
            auth_service.wechat_login_callback(db, code="code2", state=state)

    def test_login_start_then_callback_full_flow(self, db, monkeypatch, fake_redis):
        """全链路: login_start 签发 state -> 回调用同一 state 通过校验。"""
        _patch_wechat(monkeypatch, openid="wx_full_flow")
        from app.services import auth_service

        start = auth_service.wechat_login_start(redirect_uri="https://example.com/cb")
        state = start["auth_url"].split("state=")[1]
        result = auth_service.wechat_login_callback(db, code="code_x", state=state)
        assert result.need_bind is True
        assert result.openid == "wx_full_flow"


class TestWechatStateHttp:
    """HTTP 层: state 缺失 -> 400。"""

    def test_callback_missing_state_http_400(self, client, monkeypatch):
        _patch_wechat(monkeypatch)
        resp = client.get(
            "/api/v1/auth/wechat/callback",
            params={"code": "http_no_state"},
        )
        assert resp.status_code == 400

    def test_callback_bad_state_http_400(self, client, monkeypatch):
        _patch_wechat(monkeypatch)
        resp = client.get(
            "/api/v1/auth/wechat/callback",
            params={"code": "http_bad_state", "state": "forged"},
        )
        assert resp.status_code == 400
