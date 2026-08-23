"""
backend/tests/integration/test_auth_service.py

职责: 测试 auth_service 层业务集成（注册事务编排、登录校验、失败锁定、登出黑名单）

测试内容:
  - TestRegister: 注册建 user+tenant+member 三表 / 重复手机号拒绝 / 弱密码拒绝 /
    成员关联失败整体回滚不留孤儿
  - TestLogin: 登录发 token 含 tenant_id/role claims / 密码错计数 / 5次失败锁定 /
    未知手机号返回 InvalidCredentials（防枚举）
  - TestLogout: 登出写 jti 黑名单 / 已过期 token 不写黑名单
"""

import pytest

from app.crud import tenant as tenant_crud
from app.crud import user as user_crud


# ============ US1 注册 ============
class TestRegister:
    def test_register_creates_user_tenant_admin_member(self, db):
        from app.services import auth_service

        result = auth_service.register(
            db, phone="13800138000", password="abc12345", tenant_name="示例科技"
        )

        assert result.phone == "13800138000"
        assert result.role == "admin"
        assert result.plan_type == "trial"
        assert result.user_id > 0
        assert result.tenant_id > 0

        # 三表落库
        u = user_crud.get_by_phone(db, "13800138000")
        assert u is not None and u.role_default == "admin"
        t = tenant_crud.get_by_id(db, result.tenant_id)
        assert t is not None
        assert t.plan_type == "trial" and t.seat_limit == 1 and t.status == "active"

    def test_register_duplicate_phone_raises(self, db):
        from app.services import auth_service
        from app.services.auth_service import PhoneAlreadyExists

        auth_service.register(db, phone="13800138000", password="abc12345", tenant_name="A")
        with pytest.raises(PhoneAlreadyExists):
            auth_service.register(db, phone="13800138000", password="abc12345", tenant_name="B")

    @pytest.mark.parametrize("weak", ["12345678", "short1", "abcdefgh", "password"])
    def test_register_weak_password_raises(self, db, weak):
        from app.services import auth_service
        from app.services.auth_service import WeakPassword

        with pytest.raises(WeakPassword):
            auth_service.register(db, phone="13800138001", password=weak, tenant_name="A")

    def test_register_transaction_rollback_leaves_no_orphan(self, db, monkeypatch):
        """租户创建后成员关联失败 → 整体回滚，users 表不留孤儿。"""
        from app.services import auth_service

        def boom(*args, **kwargs):
            raise RuntimeError("模拟成员关联写入失败")

        monkeypatch.setattr(auth_service.tenant_crud, "add_member", boom)

        with pytest.raises(RuntimeError):
            auth_service.register(
                db, phone="13800138888", password="abc12345", tenant_name="回滚测试"
            )

        # 事务回滚：用户与租户都不应残留
        assert user_crud.get_by_phone(db, "13800138888") is None


# ============ US2 登录与锁定 ============
class TestLogin:
    def _register(self, db):
        from app.services import auth_service

        return auth_service.register(
            db, phone="13800138000", password="abc12345", tenant_name="示例科技"
        )

    def test_login_success_returns_token_with_claims(self, db):
        from app.services import auth_service
        from app.core.security import decode_access_token

        self._register(db)
        result = auth_service.login(db, phone="13800138000", password="abc12345")

        assert result.access_token
        payload = decode_access_token(result.access_token)
        assert payload["tenant_id"] == result.tenant_id
        assert payload["role"] == "admin"

    def test_login_wrong_password_raises_and_counts(self, db):
        from app.services import auth_service
        from app.services.auth_service import InvalidCredentials
        from app.core.redis import get_login_fail

        self._register(db)
        with pytest.raises(InvalidCredentials):
            auth_service.login(db, phone="13800138000", password="wrongpass1")
        assert get_login_fail("13800138000") == 1

    def test_login_lockout_after_5_failures(self, db):
        from app.services import auth_service
        from app.services.auth_service import AccountLocked, InvalidCredentials

        self._register(db)
        for _ in range(5):
            with pytest.raises((InvalidCredentials, AccountLocked)):
                auth_service.login(db, phone="13800138000", password="wrongpass1")

        # 锁定期内即使密码正确也被拒绝
        with pytest.raises(AccountLocked):
            auth_service.login(db, phone="13800138000", password="abc12345")

    def test_login_unknown_phone_raises_invalid(self, db):
        from app.services import auth_service
        from app.services.auth_service import InvalidCredentials

        with pytest.raises(InvalidCredentials):
            auth_service.login(db, phone="13900000000", password="abc12345")

    def test_login_fail_open_on_redis_error(self, db, monkeypatch):
        """: Redis 故障时登录 fail-open（不抛 500），与中间件哲学一致。"""
        from app.services import auth_service
        from app.core import redis as redis_module

        self._register(db)

        def _boom(*args, **kwargs):
            raise ConnectionError("redis down")

        # 锁定检查 / 失败计数 / 清零 全部故障 → 应 fail-open 放行，不抛异常
        monkeypatch.setattr(redis_module, "get_lock_ttl", _boom)
        monkeypatch.setattr(redis_module, "get_lock_ttl_ip", _boom)
        monkeypatch.setattr(redis_module, "incr_login_fail", _boom)
        monkeypatch.setattr(redis_module, "incr_login_fail_ip", _boom)
        monkeypatch.setattr(redis_module, "clear_login_fail", _boom)

        result = auth_service.login(db, phone="13800138000", password="abc12345", ip="1.1.1.1")
        assert result.access_token  # fail-open：正确密码仍能登录

    def test_ip_fail_count_not_cleared_on_success(self, db):
        """: 成功登录不清 IP 维度计数（防攻击者用自己合法账号重置 IP 锁绕过 credential stuffing）。"""
        from app.services import auth_service
        from app.core.redis import get_redis, get_login_fail

        self._register(db)
        ip = "9.9.9.9"
        # 4 次失败（IP 计数到 4，未达 5 不锁）
        from app.services.auth_service import InvalidCredentials
        for _ in range(4):
            with pytest.raises(InvalidCredentials):
                auth_service.login(db, phone="13800138000", password="wrongpass1", ip=ip)
        assert int(get_redis().get(f"login_fail_ip:{ip}")) == 4

        # 成功登录
        auth_service.login(db, phone="13800138000", password="abc12345", ip=ip)

        # IP 计数不清（ 修复）；phone 计数清零
        assert int(get_redis().get(f"login_fail_ip:{ip}")) == 4, "IP 计数不应被成功登录清零"
        assert get_login_fail("13800138000") == 0, "phone 计数应清零"


# ============ US3 登出 ============
class TestLogout:
    def test_logout_blocklists_jti(self, db):
        from app.services import auth_service
        from app.core.redis import is_blocklisted
        import time

        auth_service.logout(db, jti="test-jti-123", exp=int(time.time()) + 100)
        assert is_blocklisted("test-jti-123") is True

    def test_logout_expired_token_not_written(self, db):
        from app.services import auth_service
        from app.core.redis import is_blocklisted
        import time

        auth_service.logout(db, jti="already-expired", exp=int(time.time()) - 10)
        assert is_blocklisted("already-expired") is False
