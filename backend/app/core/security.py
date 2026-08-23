"""
backend/app/core/security.py

职责: bcrypt 密码哈希与校验、JWT 生成与解码、随机 Token 生成

核心设计:
  密码存储用 bcrypt 自适应哈希（不可逆 + 加盐），不存明文也不存 MD5/SHA。
  鉴权凭证用 JWT（无状态），签发时嵌入 jti（JWT ID）为将来令牌吊销预留。
  登出吊销靠 Redis 黑名单（见 redis.py），本模块只负责签发/校验。

关键技术点:
  - bcrypt vs MD5: MD5/SHA 是通用哈希，设计目标是快，GPU 暴力破解可达每秒百亿次；
    bcrypt 内置 salt + work factor（cost），故意慢（毫秒级），同等硬件下破解成本高几个数量级，
    且 cost 可随硬件升级自适应调高（passlib deprecated="auto" 会在校验时标记旧哈希需升级）。
  - JWT 无状态: 服务端不存 session，签名校验即可确认身份，水平扩展无需共享 session 存储；
    代价是令牌签发后无法主动失效，故引入 jti 黑名单做吊销。
  - jti 唯一标识: 每个令牌生成时用 secrets.token_hex(16) 产生 32 字符随机串，
    登出时将 jti 写入 Redis 黑名单 SETEX（TTL=令牌剩余有效期），鉴权时检查黑名单。
  - decode 返回 None 而非抛异常: 调用方（auth_middleware）可直接 if not payload 判断，
    不必 try/except，简化中间件代码。

依赖关系:
  - 上游: app/api/* 路由、app/core/auth_middleware（鉴权中间件调用 decode_access_token）
  - 下游: app.config（密钥/算法/过期配置）、passlib（bcrypt）、python-jose（JWT）
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# bcrypt 上下文：schemes 指定使用 bcrypt 算法；
# deprecated="auto" 表示若未来切换到更强算法，旧 bcrypt 哈希校验通过后会被标记需要 rehash，
# 但当前直接使用不影响功能
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """明文密码 -> bcrypt 哈希串（存入 User.password_hash）。

    bcrypt 内部自动生成随机 salt 并嵌入哈希结果，无需单独存 salt。
    同一密码每次哈希结果不同（因为 salt 不同），但 verify 仍能正确校验。

    Args:
        password: 用户输入的明文密码

    Returns:
        bcrypt 哈希字符串，格式如 $2b$12$...
    """
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """登录校验：明文密码是否匹配数据库中存储的 bcrypt 哈希。

    实现原理: bcrypt 从 password_hash 中提取 salt 和 cost factor，
    对输入密码做同样哈希后比对，匹配返回 True。常数时间比较防时序攻击。

    Args:
        password: 用户输入的明文密码
        password_hash: 数据库中存储的 bcrypt 哈希串

    Returns:
        True 匹配，False 不匹配
    """
    return _pwd_context.verify(password, password_hash)


def create_access_token(
    subject: str, extra_claims: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    """生成 JWT access token。

    流程: 组装 payload（sub + iat + exp + jti + 额外 claims）-> 用 app_secret_key 签名。
    设计决策: 用 HS256 对称签名（demo 单机），生产环境如需多方校验可改 RS256 非对称。

    Args:
        subject: token 主体标识，通常传 user_id（字符串形式）
        extra_claims: 额外业务 claims，如 tenant_id/role，会 merge 进 payload

    Returns:
        签名后的 JWT 字符串（header.payload.signature）
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,  # JWT 标准字段：token 主体（用户标识）
        "iss": "zhihub",  # : 签发方标识，防跨系统复用
        "aud": "zhihub-api",  # : 受众标识，防令牌被其他服务接受
        "iat": now,  # issued at：签发时间（带时区，避免 naive datetime 歧义）
        "exp": now + timedelta(
            minutes=expires_minutes if expires_minutes is not None else settings.jwt_access_token_expire_minutes
        ),  # 过期时间
        "jti": secrets.token_hex(16),  # JWT ID：每 token 唯一，用于登出黑名单吊销
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """解码并校验 JWT。

    校验项: 签名有效性（防篡改）、exp 过期时间。任一不通过即返回 None。
    设计决策: 统一返回 None 而非抛异常，调用方（auth_middleware）可直接
    if not payload 判断鉴权失败，不必 try/except，简化中间件逻辑。

    注意: 此处只校验签名和过期，不检查 jti 黑名单——黑名单检查在 auth_middleware
    中调用 redis.is_blocklisted(jti) 完成，职责分离。

    Args:
        token: 待校验的 JWT 字符串

    Returns:
        校验通过返回 payload dict；签名无效/已过期返回 None
    """
    try:
        return jwt.decode(
            token,
            settings.app_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience="zhihub-api",
            issuer="zhihub",
        )
    except JWTError:
        return None


def generate_random_token(length: int = 32) -> str:
    """生成 URL 安全的随机字符串，用于一次性凭证（邀请链接、密码重置 token 等）。

    使用 secrets.token_urlsafe（基于 os.urandom 密码学安全随机源），
    输出字符集为 URL 安全字符（A-Z a-z 0-9 - _），可直接拼入 URL。

    Args:
        length: 随机字节数（输出字符串长度约 4/3 * length）

    Returns:
        URL 安全的随机字符串
    """
    return secrets.token_urlsafe(length)
