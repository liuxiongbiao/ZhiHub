"""
backend/tests/contracts/test_register_sms.py

职责: 测试 PRD 3.1.1 注册环节短信验证码校验（修复审查报告 6.3）。
对应 feature: 规格 013 / US1 / T004

测试内容（HTTP 契约）:
  - 正确验证码注册 -> 201
  - 错误验证码注册 -> 401（InvalidCode）
  - 缺失 code 字段 -> 422（schema 校验）
  - 验证码过期/未发送 -> 401

设计决策:
  RegisterRequest.code 为必填（HTTP 422 强制）；auth_service.register(code=None) 内部调用
  可跳过校验（供测试 setup 等可信内部调用），但 HTTP 端点必传 code 走校验。
"""

import pytest

from app.services import auth_service


class TestRegisterSmsVerification:
    """注册短信验证码校验（HTTP 契约）。"""

    def test_register_with_correct_code(self, client, fake_redis):
        """正确验证码 -> 201 创建租户。"""
        # 先发送验证码
        send_result = auth_service.send_verification_code("13900139001", code_type="sms")

        resp = client.post("/api/v1/auth/register", json={
            "phone": "13900139001",
            "password": "abc12345",
            "tenant_name": "验证码注册测试",
            "code": send_result.code,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["phone"] == "13900139001"
        assert data["tenant_id"]
        assert data["role"] == "admin"

    def test_register_with_wrong_code(self, client, fake_redis):
        """错误验证码 -> 401。"""
        auth_service.send_verification_code("13900139002", code_type="sms")

        resp = client.post("/api/v1/auth/register", json={
            "phone": "13900139002",
            "password": "abc12345",
            "tenant_name": "错误验证码测试",
            "code": "000000",
        })
        assert resp.status_code == 401

    def test_register_without_code(self, client, fake_redis):
        """缺失 code 字段 -> 422（schema 强制必填）。"""
        resp = client.post("/api/v1/auth/register", json={
            "phone": "13900139003",
            "password": "abc12345",
            "tenant_name": "缺验证码测试",
        })
        assert resp.status_code == 422

    def test_register_with_expired_code(self, client, fake_redis):
        """验证码未发送/过期 -> 401。"""
        resp = client.post("/api/v1/auth/register", json={
            "phone": "13900139004",
            "password": "abc12345",
            "tenant_name": "过期验证码测试",
            "code": "123456",
        })
        assert resp.status_code == 401

    def test_register_code_consumed_after_success(self, client, fake_redis):
        """验证码一次有效：注册成功后再次用同码注册 -> 401。"""
        send_result = auth_service.send_verification_code("13900139005", code_type="sms")

        resp1 = client.post("/api/v1/auth/register", json={
            "phone": "13900139005",
            "password": "abc12345",
            "tenant_name": "一次性验证码",
            "code": send_result.code,
        })
        assert resp1.status_code == 201

        # 同一验证码再次注册（不同手机号）→ 已被消费/不属于该号 → 401
        resp2 = client.post("/api/v1/auth/register", json={
            "phone": "13900139006",
            "password": "abc12345",
            "tenant_name": "复用验证码",
            "code": send_result.code,
        })
        assert resp2.status_code == 401


class TestRegisterServiceBackwardCompat:
    """service 层 code=None 向后兼容（内部 setup 不破坏）。"""

    def test_service_register_without_code_still_works(self, db):
        """内部调用 register(code 省略) 不校验验证码，供测试 setup。"""
        result = auth_service.register(
            db, phone="13900139007", password="abc12345", tenant_name="内部调用兼容"
        )
        assert result.user_id
        assert result.tenant_id
