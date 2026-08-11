"""
backend/tests/contracts/test_pii_encryption.py

职责: 测试 PRD 3.1/4.3 PII 字段静态加密契约（AES-GCM + EncryptedString TypeDecorator）
对应日程: handover-and-plan #6

测试内容:
  - test_encrypt_decrypt_roundtrip: 加解密往返一致，且同一明文两次密文不同（nonce 随机）
  - test_phone_stored_encrypted: DB 原始列存密文（非明文 138...），含 nonce+tag
  - test_phone_read_plaintext: ORM 读取自动解密为明文
  - test_mask_pii_still_works: 脱敏函数在解密后的明文上正常工作
  - test_ticket_order_phone_encrypted: Ticket/Order 的 phone 也加密存储
  - test_phone_hash_deterministic: phone_hash 确定性（同一输入同一输出）
  - test_login_still_works: 加密后登录流程（get_by_phone 走 phone_hash）仍正常
"""

import pytest
from sqlalchemy import text

from app.core.crypto import decrypt, encrypt, phone_hash


# ═══════════════════════════════════════════════════════════
# 加解密原语
# ═══════════════════════════════════════════════════════════
class TestCryptoPrimitives:
    def test_encrypt_decrypt_roundtrip(self):
        """加解密往返: encrypt -> decrypt == 原文。"""
        plaintext = "13800000001"
        ct = encrypt(plaintext)
        assert ct != plaintext, "密文不应等于明文"
        assert decrypt(ct) == plaintext

    def test_same_plaintext_different_ciphertext(self):
        """同一明文两次加密结果不同（nonce 随机 -> 非确定性加密）。"""
        plaintext = "13800000001"
        ct1 = encrypt(plaintext)
        ct2 = encrypt(plaintext)
        assert ct1 != ct2, "AES-GCM nonce 随机，同一明文密文应不同"
        # 但都能解密回同一明文
        assert decrypt(ct1) == plaintext
        assert decrypt(ct2) == plaintext

    def test_ciphertext_is_base64(self):
        """密文是 base64 字符串（可安全存入 DB String 列）。"""
        ct = encrypt("13800000001")
        assert isinstance(ct, str)
        # base64 字符集: A-Z a-z 0-9 + / =
        import string
        allowed = set(string.ascii_letters + string.digits + "+/=")
        assert set(ct) <= allowed

    def test_phone_hash_deterministic(self):
        """phone_hash 确定性: 同一手机号两次哈希结果相同。"""
        h1 = phone_hash("13800000001")
        h2 = phone_hash("13800000001")
        assert h1 == h2
        # 不同手机号哈希不同
        assert phone_hash("13800000002") != h1

    def test_phone_hash_length(self):
        """phone_hash 是 64 字符 hex（SHA256 输出 32 字节 = 64 hex）。"""
        h = phone_hash("13800000001")
        assert len(h) == 64


# ═══════════════════════════════════════════════════════════
# ORM 透明加密（EncryptedString TypeDecorator）
# ═══════════════════════════════════════════════════════════
class TestOrmTransparentEncryption:
    def test_phone_stored_encrypted(self, db):
        """DB 原始列存密文，非明文 138...。

        用 raw SQL 绕过 ORM TypeDecorator，直接读取 DB 中的原始值。
        """
        from app.crud import user as user_crud

        user_crud.create(db, phone="13800000001", password_hash="fakehash")
        db.commit()

        # raw SQL 绕过 ORM，直接取 DB 列值
        row = db.execute(
            text("SELECT phone FROM users WHERE phone_hash = :h"),
            {"h": phone_hash("13800000001")},
        ).fetchone()
        raw_phone = row[0]

        # 密文不应包含明文手机号
        assert "13800000001" not in raw_phone
        # 密文应是 base64 字符串，长度 > 原文（含 nonce + tag）
        assert len(raw_phone) > 11

    def test_phone_read_plaintext(self, db):
        """ORM 读取自动解密: user.phone 返回明文。"""
        from app.crud import user as user_crud

        user_crud.create(db, phone="13800000001", password_hash="fakehash")
        db.commit()

        user = user_crud.get_by_phone(db, "13800000001")
        assert user is not None
        assert user.phone == "13800000001", "ORM 读取应返回明文"

    def test_phone_hash_auto_set(self, db):
        """设置 phone 时 phone_hash 自动计算（@validates）。"""
        from app.models.user import User

        u = User(phone="13800000001", password_hash="fakehash")
        assert u.phone_hash == phone_hash("13800000001")

    def test_ticket_phone_encrypted(self, db):
        """Ticket.phone 也加密存储（raw SQL 查是密文）。"""
        from app.crud.ticket import create_ticket

        t = create_ticket(
            db, tenant_id=1, ticket_no="TK-CRYPT01", title="加密测试", priority="low"
        )
        t.phone = "13800000001"
        db.commit()

        # raw SQL 取原始值
        row = db.execute(
            text("SELECT phone FROM tickets WHERE ticket_no = 'TK-CRYPT01'")
        ).fetchone()
        raw = row[0]
        assert "13800000001" not in raw, "Ticket.phone 在 DB 中应为密文"

        # ORM 读取是明文
        db.refresh(t)
        assert t.phone == "13800000001"

    def test_order_phone_encrypted(self, db):
        """Order.phone 也加密存储。"""
        from app.models.order import Order

        o = Order(
            tenant_id=1, order_no="ORD-CRYPT1", amount=99.0,
            paid_amount=99.0, status="paid", phone="13800000001",
        )
        db.add(o)
        db.commit()

        row = db.execute(
            text("SELECT phone FROM orders WHERE order_no = 'ORD-CRYPT1'")
        ).fetchone()
        raw = row[0]
        assert "13800000001" not in raw, "Order.phone 在 DB 中应为密文"

        # ORM 读取是明文
        db.refresh(o)
        assert o.phone == "13800000001"

    def test_nullable_phone_stays_null(self, db):
        """Ticket.phone 为 NULL 时，读写都保持 None（不加密 None）。"""
        from app.crud.ticket import create_ticket

        t = create_ticket(
            db, tenant_id=1, ticket_no="TK-NULL01", title="空phone", priority="low"
        )
        # 不设 phone
        db.commit()

        row = db.execute(
            text("SELECT phone FROM tickets WHERE ticket_no = 'TK-NULL01'")
        ).fetchone()
        assert row[0] is None

        db.refresh(t)
        assert t.phone is None


# ═══════════════════════════════════════════════════════════
# 脱敏兼容性 + 登录兼容性
# ═══════════════════════════════════════════════════════════
class TestPiiCompatibility:
    def test_mask_pii_still_works(self, db):
        """脱敏函数 mask_pii 在解密后的明文上正常工作。

        场景: trace_service 从 DB 读出明文 phone，调用 mask_pii 脱敏后入库 trace。
        加密层不应影响脱敏链路（脱敏在明文上操作）。
        """
        from app.crud import user as user_crud
        from app.utils.desensitize import mask_pii

        user_crud.create(db, phone="13800000001", password_hash="fakehash")
        db.commit()

        user = user_crud.get_by_phone(db, "13800000001")
        # 脱敏在明文上工作
        masked = mask_pii(user.phone)
        assert masked == "138****0001"

    def test_verify_phone_suffix_still_works(self, db):
        """双因子校验 verify_phone_suffix 在解密后的明文上正常工作。

        场景: MCP server 从 DB 读出 ticket.phone（解密为明文），与用户提供的 phone 比后4位。
        """
        from app.crud.ticket import create_ticket
        from app.mcp_servers.base_server import verify_phone_suffix

        t = create_ticket(
            db, tenant_id=1, ticket_no="TK-VRFY01", title="校验测试", priority="low"
        )
        t.phone = "13800000001"
        db.commit()
        db.refresh(t)

        # 后4位匹配
        assert verify_phone_suffix("13700000001", t.phone) is True
        # 后4位不匹配
        assert verify_phone_suffix("13900000000", t.phone) is False

    def test_login_still_works(self, db):
        """加密后登录流程仍正常: 注册 -> 登录成功。

        get_by_phone 走 phone_hash 等值查询，不受 phone 密文不确定影响。
        """
        from app.services import auth_service

        auth_service.register(
            db, phone="13800138000", password="abc12345", tenant_name="加密测试租户"
        )
        result = auth_service.login(db, phone="13800138000", password="abc12345")
        assert result.access_token
        assert result.user_id > 0

    def test_register_duplicate_phone_still_rejected(self, db):
        """加密后注册查重仍正常: 同一手机号二次注册被拒。

        phone_hash unique 约束 + get_by_phone 查重双重保障。
        """
        from app.services import auth_service
        from app.services.auth_service import PhoneAlreadyExists

        auth_service.register(
            db, phone="13800138000", password="abc12345", tenant_name="A"
        )
        with pytest.raises(PhoneAlreadyExists):
            auth_service.register(
                db, phone="13800138000", password="abc12345", tenant_name="B"
            )
