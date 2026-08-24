"""
backend/tests/unit/test_validators.py

职责: 测试 utils/validators.py 手机号格式与密码强度规则（TDD 先写测试）

测试内容:
  - TestValidatePhone: 合法手机号通过 / 太短/太长/非1开头/含字母/空 拒绝
  - TestValidatePasswordStrength: 强密码通过 / 太短/纯数字/纯字母/常见弱密码/连续序列 拒绝
"""

import pytest

from app.utils.validators import validate_password_strength, validate_phone


# ────────── 手机号格式 ──────────
class TestValidatePhone:
    @pytest.mark.parametrize("phone", ["13800138000", "15912345678", "18888888888"])
    def test_valid_phone_passes(self, phone):
        # 合法手机号不抛异常
        validate_phone(phone)

    @pytest.mark.parametrize(
        "phone",
        [
            "12345",           # 太短
            "1380013800",      # 10 位
            "138001380000",    # 12 位
            "23800138000",     # 不以 1 开头
            "1380013800a",     # 含字母
            "",                # 空
        ],
    )
    def test_invalid_phone_raises(self, phone):
        with pytest.raises(ValueError):
            validate_phone(phone)


# ────────── 密码强度 ──────────
class TestValidatePasswordStrength:
    @pytest.mark.parametrize("pw", ["abc12345", "Passw0rd", "zh1hub2026"])
    def test_strong_password_passes(self, pw):
        validate_password_strength(pw)

    def test_too_short_raises(self):
        with pytest.raises(ValueError):
            validate_password_strength("ab12")  # < 8 位

    @pytest.mark.parametrize("pw", ["A1" + "x" * 71, "密码A1" + "界" * 22])
    def test_more_than_72_utf8_bytes_raises(self, pw):
        with pytest.raises(ValueError, match="72 字节"):
            validate_password_strength(pw)

    def test_pure_digits_raises(self):
        with pytest.raises(ValueError):
            validate_password_strength("12345678")  # 纯数字

    def test_pure_letters_raises(self):
        with pytest.raises(ValueError):
            validate_password_strength("abcdefgh")  # 无数字

    @pytest.mark.parametrize("pw", ["password", "88888888", "12345678"])
    def test_common_weak_password_raises(self, pw):
        with pytest.raises(ValueError):
            validate_password_strength(pw)

    def test_sequential_chars_raises(self):
        with pytest.raises(ValueError):
            validate_password_strength("abcdefg1")  # 连续字母序列
