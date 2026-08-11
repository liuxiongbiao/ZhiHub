"""
backend/tests/contracts/test_account_ext.py

职责: 测试 PRD 3.1 账户体系扩展 8 项契约（#10-#17，不含#12微信）
对应日程: PRD 3.1 账户体系扩展

测试内容:
  - TestSendVerificationCode: 验证码发送 + 存储 Redis + 频率限制（1min/次、24h/10次）
  - TestCodeBruteForce: 验证码 3 次错误失效（防爆破）
  - TestSmsLogin: 验证码登录（正确码/错误码/过期码/未知手机号防枚举）
  - TestEmailRegister: 邮箱注册（建 user+tenant / 重复邮箱 / 企业版 pending_review）
  - TestEmailLogin: 邮箱密码登录（正确/错误密码）
  - TestPasswordReset: 密码找回（验证码验证身份 -> 重置密码）
  - TestBindEmail: 账号绑定邮箱（验证码 + 唯一性校验）
  - TestEnterpriseApprove: 企业版审批（pending_review -> active）

设计说明（面试点）:
  - 验证码写 Redis（demo mock 短信，不接阿里云），TTL 5min
  - 频率限制: 1min/次（vcode_freq key TTL=60s）、24h/10次（vcode_daily INCR + TTL=86400s）
  - 防爆破: 3 次错误验证码 -> 码失效（DEL vcode key）
  - 防枚举: 验证码登录时用户不存在与验证码错误返回同一异常
"""

import pytest

from app.core import redis as redis_module
from app.crud import tenant as tenant_crud
from app.crud import user as user_crud


# ─── 共享夹具 ───
@pytest.fixture()
def registered_user(db):
    """注册一个手机号用户（供验证码登录测试用）。"""
    from app.services import auth_service

    return auth_service.register(
        db, phone="13800138000", password="abc12345", tenant_name="示例科技"
    )


@pytest.fixture()
def email_user(db):
    """注册一个邮箱用户（供邮箱登录/密码找回/绑定测试用）。"""
    from app.services import auth_service

    return auth_service.register_by_email(
        db, email="user@example.com", password="abc12345", tenant_name="邮箱科技"
    )


# ═══════════════════════════════════════════════════════════
# 验证码发送 + 频率限制（#10 send_code / #14 短信频率限制）
# ═══════════════════════════════════════════════════════════
class TestSendVerificationCode:
    """验证码发送: 写 Redis + 频率限制 1min/次、24h/10次。"""

    def test_send_sms_code_stores_in_redis(self, fake_redis):
        """发送短信验证码 -> 6 位码写入 Redis，TTL 5min。"""
        from app.services import auth_service

        result = auth_service.send_verification_code("13800138000", code_type="sms")

        assert len(result.code) == 6
        assert result.expires_in == 300
        # Redis 中存有验证码
        stored = fake_redis.get("vcode:sms:13800138000")
        assert stored == result.code

    def test_send_email_code_stores_in_redis(self, fake_redis):
        """发送邮箱验证码 -> 写入 Redis。"""
        from app.services import auth_service

        result = auth_service.send_verification_code("user@example.com", code_type="email")

        assert len(result.code) == 6
        stored = fake_redis.get("vcode:email:user@example.com")
        assert stored == result.code

    def test_freq_limit_1min(self, fake_redis):
        """1 分钟内重复发送 -> RateLimitExceeded。"""
        from app.services import auth_service
        from app.services.auth_service import RateLimitExceeded

        auth_service.send_verification_code("13800138001", code_type="sms")
        with pytest.raises(RateLimitExceeded):
            auth_service.send_verification_code("13800138001", code_type="sms")

    def test_freq_limit_resets_after_1min(self, fake_redis):
        """1 分钟后频率限制 key 过期 -> 可再次发送。"""
        from app.services import auth_service

        auth_service.send_verification_code("13800138002", code_type="sms")
        # 模拟 1 分钟后频率限制 key 过期
        fake_redis.delete("vcode_freq:sms:13800138002")
        result = auth_service.send_verification_code("13800138002", code_type="sms")
        assert len(result.code) == 6

    def test_daily_limit_10(self, fake_redis):
        """24 小时内发送 11 次 -> 第 11 次 RateLimitExceeded。"""
        from app.services import auth_service
        from app.services.auth_service import RateLimitExceeded

        for i in range(10):
            # 每次清除 1min 频率限制 key 以模拟间隔发送
            fake_redis.delete("vcode_freq:sms:13800138003")
            auth_service.send_verification_code("13800138003", code_type="sms")

        fake_redis.delete("vcode_freq:sms:13800138003")
        with pytest.raises(RateLimitExceeded):
            auth_service.send_verification_code("13800138003", code_type="sms")

    def test_new_code_overwrites_old(self, fake_redis):
        """重新发送验证码 -> 旧码被新码覆盖，失败计数清零。"""
        from app.services import auth_service

        r1 = auth_service.send_verification_code("13800138004", code_type="sms")
        # 模拟 1 次错误验证
        fake_redis.incr("vcode_fail:sms:13800138004")
        # 清频率限制后重发
        fake_redis.delete("vcode_freq:sms:13800138004")
        r2 = auth_service.send_verification_code("13800138004", code_type="sms")

        assert r2.code != r1.code
        # 失败计数应被清零
        assert fake_redis.get("vcode_fail:sms:13800138004") is None


# ═══════════════════════════════════════════════════════════
# 验证码防爆破（#13 3 次错误失效）
# ═══════════════════════════════════════════════════════════
class TestCodeBruteForce:
    """验证码 3 次错误 -> 码失效（防爆破）。"""

    def test_3_wrong_attempts_invalidates_code(self, fake_redis):
        """3 次错误验证码后，码被删除，即使之后用正确码也失败。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCode

        result = auth_service.send_verification_code("13800138005", code_type="sms")
        correct_code = result.code

        # 3 次错误验证
        for _ in range(3):
            with pytest.raises(InvalidCode):
                auth_service.verify_code("13800138005", "000000", code_type="sms")

        # 码已失效，正确码也无法验证
        with pytest.raises(InvalidCode):
            auth_service.verify_code("13800138005", correct_code, code_type="sms")

    def test_2_wrong_attempts_code_still_valid(self, fake_redis):
        """2 次错误后码仍有效，第 3 次正确码可验证成功。"""
        from app.services import auth_service

        result = auth_service.send_verification_code("13800138006", code_type="sms")
        correct_code = result.code

        # 2 次错误验证（不触发失效）
        for _ in range(2):
            try:
                auth_service.verify_code("13800138006", "000000", code_type="sms")
            except Exception:
                pass

        # 第 3 次正确码 -> 验证成功
        assert auth_service.verify_code("13800138006", correct_code, code_type="sms") is True


# ═══════════════════════════════════════════════════════════
# 验证码登录（#10 login_by_code）
# ═══════════════════════════════════════════════════════════
class TestSmsLogin:
    """验证码登录: 正确码登录 / 错误码 / 过期码 / 未知手机号防枚举。"""

    def test_login_by_code_success(self, db, registered_user, fake_redis):
        """正确验证码登录 -> 返回 token。"""
        from app.services import auth_service
        from app.core.security import decode_access_token

        result = auth_service.send_verification_code("13800138000", code_type="sms")
        login_result = auth_service.login_by_code(db, phone="13800138000", code=result.code)

        assert login_result.access_token
        payload = decode_access_token(login_result.access_token)
        assert payload["sub"] == str(registered_user.user_id)

    def test_login_by_wrong_code(self, db, registered_user, fake_redis):
        """错误验证码登录 -> InvalidCode。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCode

        auth_service.send_verification_code("13800138000", code_type="sms")
        with pytest.raises(InvalidCode):
            auth_service.login_by_code(db, phone="13800138000", code="000000")

    def test_login_by_expired_code(self, db, registered_user, fake_redis):
        """验证码过期（Redis key 不存在）-> InvalidCode。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCode

        auth_service.send_verification_code("13800138000", code_type="sms")
        # 模拟验证码过期
        fake_redis.delete("vcode:sms:13800138000")
        with pytest.raises(InvalidCode):
            auth_service.login_by_code(db, phone="13800138000", code="123456")

    def test_login_unknown_phone_same_error(self, db, fake_redis):
        """未注册手机号验证码登录 -> InvalidCode（不暴露用户是否存在）。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCode

        # 发送验证码（即使手机号未注册也允许发送，防枚举）
        auth_service.send_verification_code("13900000000", code_type="sms")
        # 获取正确验证码
        code = fake_redis.get("vcode:sms:13900000000")
        with pytest.raises(InvalidCode):
            auth_service.login_by_code(db, phone="13900000000", code=code)


# ═══════════════════════════════════════════════════════════
# 邮箱注册 + 登录（#11）
# ═══════════════════════════════════════════════════════════
class TestEmailRegister:
    """邮箱注册: 建 user+tenant / 重复邮箱 / 企业版 pending_review。"""

    def test_register_by_email_creates_user_tenant(self, db):
        """邮箱注册 -> 创建 user（含 email）+ trial tenant + admin member。"""
        from app.services import auth_service

        result = auth_service.register_by_email(
            db, email="new@example.com", password="abc12345", tenant_name="新公司"
        )

        assert result.email == "new@example.com"
        assert result.role == "admin"
        assert result.plan_type == "trial"
        assert result.user_id > 0
        assert result.tenant_id > 0

        # user 落库，email 可查
        u = user_crud.get_by_email(db, "new@example.com")
        assert u is not None
        assert u.email == "new@example.com"

    def test_register_duplicate_email_raises(self, db, email_user):
        """重复邮箱注册 -> EmailAlreadyExists。"""
        from app.services import auth_service
        from app.services.auth_service import EmailAlreadyExists

        with pytest.raises(EmailAlreadyExists):
            auth_service.register_by_email(
                db, email="user@example.com", password="abc12345", tenant_name="B"
            )

    def test_register_enterprise_pending_review(self, db):
        """企业版注册 -> tenant.status=pending_review（不自动激活）。"""
        from app.services import auth_service

        result = auth_service.register_by_email(
            db, email="ent@example.com", password="abc12345",
            tenant_name="企业版公司", plan_type="enterprise"
        )

        assert result.plan_type == "enterprise"
        tenant = tenant_crud.get_by_id(db, result.tenant_id)
        assert tenant.status == "pending_review"

    def test_register_weak_password_raises(self, db):
        """邮箱注册弱密码 -> WeakPassword。"""
        from app.services import auth_service
        from app.services.auth_service import WeakPassword

        with pytest.raises(WeakPassword):
            auth_service.register_by_email(
                db, email="weak@example.com", password="12345678", tenant_name="弱密码公司"
            )


class TestEmailLogin:
    """邮箱密码登录: 正确/错误密码。"""

    def test_login_by_email_success(self, db, email_user):
        """正确邮箱+密码登录 -> 返回 token。"""
        from app.services import auth_service
        from app.core.security import decode_access_token

        result = auth_service.login_by_email(db, email="user@example.com", password="abc12345")

        assert result.access_token
        payload = decode_access_token(result.access_token)
        assert payload["sub"] == str(email_user.user_id)

    def test_login_by_email_wrong_password(self, db, email_user):
        """错误密码登录 -> InvalidCredentials。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCredentials

        with pytest.raises(InvalidCredentials):
            auth_service.login_by_email(db, email="user@example.com", password="wrongpass1")

    def test_login_by_email_unknown_email(self, db):
        """未注册邮箱登录 -> InvalidCredentials（防枚举）。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCredentials

        with pytest.raises(InvalidCredentials):
            auth_service.login_by_email(db, email="nobody@example.com", password="abc12345")


# ═══════════════════════════════════════════════════════════
# 密码找回（#16 验证码验证身份 -> 重置密码）
# ═══════════════════════════════════════════════════════════
class TestPasswordReset:
    """密码找回: 验证码验证身份 -> 重置密码 -> 新密码可登录。"""

    def test_reset_password_by_sms(self, db, registered_user, fake_redis):
        """短信验证码重置密码 -> 新密码可登录，旧密码失效。"""
        from app.services import auth_service

        result = auth_service.send_verification_code("13800138000", code_type="sms")
        auth_service.reset_password(
            db, target="13800138000", code=result.code,
            new_password="newpass123", code_type="sms"
        )

        # 新密码可登录
        login_result = auth_service.login(db, phone="13800138000", password="newpass123")
        assert login_result.access_token

    def test_reset_password_by_email(self, db, email_user, fake_redis):
        """邮箱验证码重置密码 -> 新密码可登录。"""
        from app.services import auth_service

        result = auth_service.send_verification_code("user@example.com", code_type="email")
        auth_service.reset_password(
            db, target="user@example.com", code=result.code,
            new_password="newpass123", code_type="email"
        )

        # 新密码可登录
        login_result = auth_service.login_by_email(
            db, email="user@example.com", password="newpass123"
        )
        assert login_result.access_token

    def test_reset_password_wrong_code(self, db, registered_user, fake_redis):
        """错误验证码重置密码 -> InvalidCode。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCode

        auth_service.send_verification_code("13800138000", code_type="sms")
        with pytest.raises(InvalidCode):
            auth_service.reset_password(
                db, target="13800138000", code="000000",
                new_password="newpass123", code_type="sms"
            )

    def test_reset_password_weak_new_password(self, db, registered_user, fake_redis):
        """新密码强度不足 -> WeakPassword。"""
        from app.services import auth_service
        from app.services.auth_service import WeakPassword

        result = auth_service.send_verification_code("13800138000", code_type="sms")
        with pytest.raises(WeakPassword):
            auth_service.reset_password(
                db, target="13800138000", code=result.code,
                new_password="12345678", code_type="sms"
            )


# ═══════════════════════════════════════════════════════════
# 账号绑定邮箱（#15）
# ═══════════════════════════════════════════════════════════
class TestBindEmail:
    """账号绑定邮箱: 验证码 + 唯一性校验。"""

    def test_bind_email_success(self, db, registered_user, fake_redis):
        """手机号注册用户绑定邮箱 -> 验证码通过后 email 写入 user。"""
        from app.services import auth_service

        result = auth_service.send_verification_code("bind@example.com", code_type="email")
        auth_service.bind_email(
            db, user_id=registered_user.user_id,
            email="bind@example.com", code=result.code
        )

        u = user_crud.get_by_id(db, registered_user.user_id)
        assert u.email == "bind@example.com"

    def test_bind_already_used_email(self, db, registered_user, email_user, fake_redis):
        """绑定已被其他用户占用的邮箱 -> EmailAlreadyExists。"""
        from app.services import auth_service
        from app.services.auth_service import EmailAlreadyExists

        result = auth_service.send_verification_code("user@example.com", code_type="email")
        with pytest.raises(EmailAlreadyExists):
            auth_service.bind_email(
                db, user_id=registered_user.user_id,
                email="user@example.com", code=result.code
            )

    def test_bind_email_wrong_code(self, db, registered_user, fake_redis):
        """错误验证码绑定邮箱 -> InvalidCode。"""
        from app.services import auth_service
        from app.services.auth_service import InvalidCode

        auth_service.send_verification_code("bind2@example.com", code_type="email")
        with pytest.raises(InvalidCode):
            auth_service.bind_email(
                db, user_id=registered_user.user_id,
                email="bind2@example.com", code="000000"
            )


# ═══════════════════════════════════════════════════════════
# 企业版审批（#17）
# ═══════════════════════════════════════════════════════════
class TestEnterpriseApprove:
    """企业版审批: pending_review -> active。"""

    def test_approve_pending_enterprise(self, db):
        """审批 pending_review 企业 -> status 变 active。"""
        from app.services import auth_service
        from app.services import tenant_service

        reg = auth_service.register_by_email(
            db, email="approve@example.com", password="abc12345",
            tenant_name="待审批企业", plan_type="enterprise"
        )
        tenant = tenant_crud.get_by_id(db, reg.tenant_id)
        assert tenant.status == "pending_review"

        result = tenant_service.approve_enterprise(db, reg.tenant_id)
        assert result["status"] == "active"

        tenant = tenant_crud.get_by_id(db, reg.tenant_id)
        assert tenant.status == "active"

    def test_approve_non_pending_raises(self, db, registered_user):
        """审批非 pending_review 租户 -> TenantNotPending。"""
        from app.services import tenant_service

        with pytest.raises(tenant_service.TenantNotPending):
            tenant_service.approve_enterprise(db, registered_user.tenant_id)

    def test_approve_unknown_tenant_raises(self, db):
        """审批不存在的租户 -> TenantNotFound。"""
        from app.services import tenant_service

        with pytest.raises(tenant_service.TenantNotFound):
            tenant_service.approve_enterprise(db, tenant_id=999999)


# ═══════════════════════════════════════════════════════════
# API 端点契约测试
# ═══════════════════════════════════════════════════════════
class TestAccountExtAPI:
    """API 层端点契约: HTTP 状态码 + 响应结构。"""

    def test_sms_send_endpoint(self, client, fake_redis):
        """POST /api/v1/auth/sms/send -> 200，返回验证码（demo）。"""
        resp = client.post("/api/v1/auth/sms/send", json={"phone": "13800139000"})
        assert resp.status_code == 200
        data = resp.json()
        assert "code" in data
        assert len(data["code"]) == 6

    def test_sms_send_freq_limit_429(self, client, fake_redis):
        """1 分钟内重复发送 -> 429。"""
        client.post("/api/v1/auth/sms/send", json={"phone": "13800139001"})
        resp = client.post("/api/v1/auth/sms/send", json={"phone": "13800139001"})
        assert resp.status_code == 429

    def test_sms_login_endpoint(self, client, db, fake_redis):
        """POST /api/v1/auth/login/sms -> 200 + access_token。"""
        from app.services import auth_service

        auth_service.register(db, phone="13800138007", password="abc12345", tenant_name="API测试")
        send_result = auth_service.send_verification_code("13800138007", code_type="sms")

        resp = client.post("/api/v1/auth/login/sms", json={
            "phone": "13800138007", "code": send_result.code
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_sms_login_wrong_code_401(self, client, db, fake_redis):
        """错误验证码登录 -> 401。"""
        from app.services import auth_service

        auth_service.register(db, phone="13800138008", password="abc12345", tenant_name="API测试2")
        auth_service.send_verification_code("13800138008", code_type="sms")

        resp = client.post("/api/v1/auth/login/sms", json={
            "phone": "13800138008", "code": "000000"
        })
        assert resp.status_code == 401

    def test_email_register_endpoint(self, client):
        """POST /api/v1/auth/register/email -> 201。"""
        resp = client.post("/api/v1/auth/register/email", json={
            "email": "apireg@example.com", "password": "abc12345", "tenant_name": "API注册"
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "apireg@example.com"
        assert data["plan_type"] == "trial"

    def test_email_register_enterprise_endpoint(self, client):
        """企业版注册 -> 201 + plan_type=enterprise。"""
        resp = client.post("/api/v1/auth/register/email", json={
            "email": "apient@example.com", "password": "abc12345",
            "tenant_name": "API企业版", "plan_type": "enterprise"
        })
        assert resp.status_code == 201
        assert resp.json()["plan_type"] == "enterprise"

    def test_email_register_duplicate_409(self, client, db):
        """重复邮箱注册 -> 409。"""
        from app.services import auth_service

        auth_service.register_by_email(
            db, email="dup@example.com", password="abc12345", tenant_name="已有"
        )
        resp = client.post("/api/v1/auth/register/email", json={
            "email": "dup@example.com", "password": "abc12345", "tenant_name": "重复"
        })
        assert resp.status_code == 409

    def test_email_login_endpoint(self, client, db):
        """POST /api/v1/auth/login/email -> 200 + access_token。"""
        from app.services import auth_service

        auth_service.register_by_email(
            db, email="apilogin@example.com", password="abc12345", tenant_name="API登录"
        )
        resp = client.post("/api/v1/auth/login/email", json={
            "email": "apilogin@example.com", "password": "abc12345"
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_email_login_wrong_password_401(self, client, db):
        """邮箱登录密码错误 -> 401。"""
        from app.services import auth_service

        auth_service.register_by_email(
            db, email="apiwrong@example.com", password="abc12345", tenant_name="API错误"
        )
        resp = client.post("/api/v1/auth/login/email", json={
            "email": "apiwrong@example.com", "password": "wrongpass1"
        })
        assert resp.status_code == 401

    def test_email_login_ip_rate_limit_429(self, client, fake_redis):
        """M3: /login/email IP 限流 30/min，第 31 次 -> 429（此前该端点无任何暴力破解防护）。"""
        body = {"email": "nobody@example.com", "password": "whatever"}
        # 前 30 次：限流放行（凭据无效 -> 401）
        for _ in range(30):
            r = client.post("/api/v1/auth/login/email", json=body)
            assert r.status_code == 401, f"前 30 次应放行: {r.status_code} {r.text}"
        # 第 31 次：IP 限流 -> 429
        r = client.post("/api/v1/auth/login/email", json=body)
        assert r.status_code == 429, f"第 31 次应限流: {r.status_code} {r.text}"

    def test_password_reset_endpoint(self, client, db, fake_redis):
        """POST /api/v1/auth/password/reset -> 200。"""
        from app.services import auth_service

        auth_service.register(db, phone="13800138009", password="abc12345", tenant_name="重置测试")
        send_result = auth_service.send_verification_code("13800138009", code_type="sms")

        resp = client.post("/api/v1/auth/password/reset", json={
            "target": "13800138009", "code": send_result.code,
            "new_password": "newpass123", "code_type": "sms"
        })
        assert resp.status_code == 200

    def test_enterprise_approve_endpoint(self, client, db, make_token):
        """POST /api/v1/tenant/enterprise/approve -> 200，status=active。"""
        from app.services import auth_service

        # 注册一个 trial 租户的 admin（作为审批者）
        admin_reg = auth_service.register(
            db, phone="13800138010", password="abc12345", tenant_name="审批方"
        )
        token = make_token(admin_reg.user_id, admin_reg.tenant_id, "admin")

        # 注册一个企业版租户（待审批）
        ent_reg = auth_service.register_by_email(
            db, email="entapprove@example.com", password="abc12345",
            tenant_name="待审批企业", plan_type="enterprise"
        )

        resp = client.post(
            "/api/v1/tenant/enterprise/approve",
            json={"tenant_id": ent_reg.tenant_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_enterprise_approve_non_admin_403(self, client, db, make_token):
        """非 admin 角色审批 -> 403。"""
        from app.services import auth_service

        admin_reg = auth_service.register(
            db, phone="13800138011", password="abc12345", tenant_name="主租户"
        )
        # agent token
        token = make_token(admin_reg.user_id, admin_reg.tenant_id, "agent")

        ent_reg = auth_service.register_by_email(
            db, email="ent403@example.com", password="abc12345",
            tenant_name="企业版403", plan_type="enterprise"
        )

        resp = client.post(
            "/api/v1/tenant/enterprise/approve",
            json={"tenant_id": ent_reg.tenant_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_bind_email_endpoint(self, client, db, fake_redis, make_token):
        """POST /api/v1/auth/bind/email -> 200（需登录）。"""
        from app.services import auth_service

        reg = auth_service.register(
            db, phone="13800138012", password="abc12345", tenant_name="绑定测试"
        )
        token = make_token(reg.user_id, reg.tenant_id, "admin")
        send_result = auth_service.send_verification_code("apibind@example.com", code_type="email")

        resp = client.post(
            "/api/v1/auth/bind/email",
            json={"email": "apibind@example.com", "code": send_result.code},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_bind_email_unauth_401(self, client):
        """未认证绑定邮箱 -> 401。"""
        resp = client.post("/api/v1/auth/bind/email", json={
            "email": "noauth@example.com", "code": "123456"
        })
        assert resp.status_code == 401
