"""
backend/app/core/crypto.py

职责: PII 字段静态加密（AES-GCM）+ 确定性哈希（HMAC-SHA256，用于手机号等值查询）

核心设计:
  手机号等 PII 字段在 DB 中必须以密文存储，防止 DB 泄露时明文外流。
  采用 AES-256-GCM（认证加密：机密性 + 完整性），密钥从 settings.app_secret_key
  经 SHA256 派生（截 32 字节）， nonce 每次随机 12 字节，密文 = base64(nonce || ct || tag)。

  难点: AES-GCM 的 nonce 随机 -> 密文不确定 -> 同一明文两次加密结果不同，
  导致 WHERE phone = ? 等值查询失效（登录查重依赖此查询）。
  解法: 额外维护 phone_hash 列（HMAC-SHA256，确定性），查询走 phone_hash 而非密文。
  HMAC 而非裸 SHA256: HMAC 带密钥，防彩虹表/离线碰撞，且密钥与加密密钥同源。

  EncryptedString(TypeDecorator): SQLAlchemy 透明层，ORM 写时加密、读时解密，
  业务代码无感知（user.phone 读出来就是明文）。

关键技术点:
  - AES-GCM 认证加密: 同时保证机密性（加密）和完整性（GCM tag），篡改密文解密会抛 InvalidTag
  - nonce 随机性: GCM 要求同一密钥下 nonce 不重复，os.urandom(12) 提供密码学安全随机
  - 密钥派生: SHA256(secret) 截 32 字节，demo 阶段从 app_secret_key 派生（生产应走 KMS）
  - HMAC-SHA256 确定性哈希: 用于 phone 等值查询/唯一约束，密文本身不可查
  - TypeDecorator 透明层: process_bind_param(写加密) + process_result_value(读解密)

依赖关系:
  - 上游: app.models.user/ticket/order（EncryptedString 列类型）、app.crud.user（phone_hash 查询）
  - 下游: app.config（app_secret_key）、cryptography（AESGCM）
"""

import base64
import hashlib
import hmac
import os

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.config import settings


def _derive_key() -> bytes:
    """从 settings.app_secret_key 派生 32 字节 AES 密钥（SHA256 截断）。

    SHA256 输出正好 32 字节，恰好满足 AES-256 密钥长度要求。
    不用 PBKDF2/scrypt: demo 阶段密钥已由运维通过 APP_SECRET_KEY 注入，
    派生函数只需固定映射即可；生产环境应走 KMS 托管密钥。
    """
    return hashlib.sha256(settings.app_secret_key.encode("utf-8")).digest()


# 模块级密钥（进程启动时派生一次，后续加解密复用）
_key = _derive_key()


def encrypt(plaintext: str) -> str:
    """AES-256-GCM 加密，返回 base64(nonce || ciphertext || tag)。

    流程:
      1. 生成 12 字节随机 nonce（GCM 推荐 96-bit）
      2. AESGCM.encrypt 返回 ciphertext || tag（cryptography 库将 16 字节 tag 追加在密文末尾）
      3. 拼接 nonce + ciphertext_tag，base64 编码为字符串存入 DB

    设计决策:
      nonce 随机 -> 同一明文每次加密结果不同（非确定性），防密文比对攻击。
      代价: 无法在 DB 层做等值查询/唯一约束，故引入 phone_hash 列解决查询问题。

    Args:
        plaintext: 明文字符串（如手机号 "13800000001"）
    Returns:
        base64 编码的密文字符串（含 nonce + tag），可直接存入 DB 的 String 列
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(_key)
    nonce = os.urandom(12)  # 96-bit nonce，GCM 标准推荐长度
    # cryptography 的 AESGCM.encrypt 返回 ciphertext + 16 字节 GCM tag 拼接
    ct_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    blob = nonce + ct_with_tag
    return base64.b64encode(blob).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """AES-256-GCM 解密，输入 base64(nonce || ciphertext || tag)，返回明文。

    流程:
      1. base64 解码
      2. 前 12 字节是 nonce，其余是 ciphertext || tag
      3. AESGCM.decrypt 验证 tag 并解密，tag 不匹配抛 InvalidTag（密文被篡改）

    Args:
        ciphertext: encrypt() 返回的 base64 字符串
    Returns:
        明文字符串
    Raises:
        cryptography.exceptions.InvalidTag: 密文被篡改或密钥不匹配
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(_key)
    blob = base64.b64decode(ciphertext)
    nonce = blob[:12]
    ct_with_tag = blob[12:]
    return aesgcm.decrypt(nonce, ct_with_tag, None).decode("utf-8")


def phone_hash(phone: str) -> str:
    """手机号的确定性哈希（HMAC-SHA256），用于等值查询和唯一约束。

    设计决策:
      AES-GCM 密文不确定（nonce 随机），无法做 WHERE phone = ? 查询。
      HMAC-SHA256 是确定性的: 同一输入 + 同一密钥 -> 同一输出，可索引可比较。
      用 HMAC 而非裸 SHA256: HMAC 带密钥参与，攻击者即使拿到 DB 也无法离线预计算
      全量手机号哈希做反查（需要知道 _key 才能构造相同的 HMAC）。

    Args:
        phone: 明文手机号
    Returns:
        64 字符 hex 字符串（SHA256 输出 32 字节 = 64 hex）
    """
    return hmac.new(_key, phone.encode("utf-8"), hashlib.sha256).hexdigest()


def pii_hash(value: str) -> str:
    """通用 PII 确定性哈希（HMAC-SHA256），用于邮箱等字段的等值查询和唯一约束。

    与 phone_hash 同理: AES-GCM 密文不确定，无法做 WHERE email = ? 查询。
    HMAC-SHA256 确定性哈希解决此问题。phone_hash 是本函数的语义别名（同算法同密钥）。

    Args:
        value: 明文字符串（如邮箱 "user@example.com"）
    Returns:
        64 字符 hex 字符串（SHA256 输出 32 字节 = 64 hex）
    """
    return hmac.new(_key, value.encode("utf-8"), hashlib.sha256).hexdigest()


class EncryptedString(TypeDecorator):
    """SQLAlchemy TypeDecorator: 写时 AES-GCM 加密，读时解密，对 ORM 透明。

    用法: phone: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    效果:
      - ORM 写入 user.phone = "138..." -> DB 存密文 base64 字符串
      - ORM 读取 user.phone -> 自动解密返回 "138..." 明文
      - 业务代码无感知，verify_phone_suffix(user.phone) 拿到的是明文

    设计决策:
      impl = String: 底层用 String 存储 base64 密文（长度需容纳 nonce+ct+tag 的 base64 编码）
      cache_ok = True: 告诉 SQLAlchemy 此类型的实例可安全缓存（无方言相关状态）
      None 透传: nullable 列的 NULL 值不加密，直接存 NULL
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """ORM 写入时调用: 明文 -> 密文。"""
        if value is None:
            return None
        return encrypt(value)

    def process_result_value(self, value, dialect):
        """ORM 读取时调用: 密文 -> 明文。"""
        if value is None:
            return None
        return decrypt(value)
