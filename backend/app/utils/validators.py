r"""
backend/app/utils/validators.py

职责: 手机号格式校验、密码强度校验（纯函数，不合法 raise ValueError）
对应日程: 001-account-tenant

核心设计:
  规则来源 PRD V1.4 §3.1.2：手机号中国大陆 11 位（1 开头第二位 3-9）；
  密码 ≥8 位、同时含字母和数字、拒纯数字/纯字母/连续字符/常见弱密码。
  不合法的 ValueError 被 auth_service 统一转为 WeakPassword 业务异常。

  validate_phone()     -> 正则 ^1[3-9]\d{9}$，不匹配抛 ValueError
  validate_password_strength()
    ├─ 长度 < 8       -> ValueError
    ├─ 纯数字/纯字母  -> ValueError
    ├─ 在弱密码黑名单 -> ValueError（password、88888888、12345678 等）
    └─ 含连续6字符    -> ValueError（如 abcdefg、1234567）
"""

import re

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

# 邮箱格式：local@domain.tld（PRD 3.1 #11 邮箱注册/登录）
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# 常见弱密码黑名单（PRD 点名 password / 88888888；补充若干高频弱密码）
_WEAK_PASSWORDS = {
    "password",
    "12345678",
    "88888888",
    "00000000",
    "11111111",
    "123456789",
    "qwertyui",
    "abc123456",
}


def validate_phone(phone: str) -> None:
    """手机号格式校验，不合法抛 ValueError。"""
    if not phone or not _PHONE_RE.match(phone):
        raise ValueError("手机号格式不正确")


def validate_email(email: str) -> None:
    """邮箱格式校验，不合法抛 ValueError（PRD 3.1 #11）。"""
    if not email or not _EMAIL_RE.match(email):
        raise ValueError("邮箱格式不正确")


def _has_sequential_run(pw: str, run: int = 6) -> bool:
    """检测连续字符序列（如 123456 / abcdef）。run 为触发的连续长度阈值。"""
    lowered = pw.lower()
    asc = desc = 1
    for i in range(1, len(lowered)):
        diff = ord(lowered[i]) - ord(lowered[i - 1])
        asc = asc + 1 if diff == 1 else 1
        desc = desc + 1 if diff == -1 else 1
        if asc >= run or desc >= run:
            return True
    return False


def validate_password_strength(pw: str) -> None:
    """密码强度校验，不合法抛 ValueError（含具体原因）。"""
    if pw is None or len(pw) < 8:
        raise ValueError("密码长度至少 8 位")
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise ValueError("密码需同时包含字母和数字")
    if pw.lower() in _WEAK_PASSWORDS:
        raise ValueError("密码过于常见，请更换")
    if _has_sequential_run(pw):
        raise ValueError("密码不能包含连续字符序列")
