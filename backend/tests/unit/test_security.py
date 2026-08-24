"""
backend/tests/unit/test_security.py

职责: 回归保护 core/security.py 的 bcrypt 哈希 + JWT 生成/解码/过期处理

测试内容:
  - TestPasswordHashing: hash != 明文 + verify 正确/错误密码
  - TestJWT: 生成/解码 roundtrip 含 sub/tenant_id/role/jti/exp / 非法 token None /
    过期 token None / 每次生成唯一 jti（黑名单需要）
"""

from datetime import timedelta

from jose import jwt

from app.config import settings
from app.core import security


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        h = security.hash_password("abc12345")
        assert h != "abc12345"  # 不是明文
        assert security.verify_password("abc12345", h) is True

    def test_verify_wrong_password_fails(self):
        h = security.hash_password("abc12345")
        assert security.verify_password("wrongpass", h) is False

    def test_invalid_hash_fails_closed(self):
        assert security.verify_password("abc12345", "not-a-bcrypt-hash") is False

    def test_existing_bcrypt_hash_remains_compatible(self):
        existing = "$2b$12$Fn40TiOzVNE8o7qZhEGT/ODdGpw0h6z1yBb/XPmVY79QJn2vegfGW"
        assert security.verify_password("abc12345", existing) is True

    def test_password_over_bcrypt_limit_is_rejected(self):
        try:
            security.hash_password("A1" + "x" * 71)
        except ValueError as error:
            assert "72 字节" in str(error)
        else:
            raise AssertionError("超过 bcrypt 上限的密码必须被拒绝")


class TestJWT:
    def test_create_and_decode_roundtrip(self):
        token = security.create_access_token(
            subject="42", extra_claims={"tenant_id": 7, "role": "admin"}
        )
        payload = security.decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["tenant_id"] == 7
        assert payload["role"] == "admin"
        assert "jti" in payload  # 黑名单需要 jti
        assert "exp" in payload

    def test_decode_invalid_token_returns_none(self):
        assert security.decode_access_token("not.a.jwt") is None

    def test_decode_expired_token_returns_none(self):
        # 手工签一个已过期的 token
        from datetime import datetime, timezone

        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        token = jwt.encode(
            {"sub": "1", "exp": past, "jti": "x"},
            settings.app_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        assert security.decode_access_token(token) is None

    def test_each_token_has_unique_jti(self):
        t1 = security.decode_access_token(security.create_access_token("1"))
        t2 = security.decode_access_token(security.create_access_token("1"))
        assert t1["jti"] != t2["jti"]
