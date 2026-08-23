"""阿里云短信客户端模式选择、配置完整性与真实请求契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import sms_client


def _configure_real(monkeypatch) -> None:
    monkeypatch.setattr(sms_client.settings, "app_env", "development")
    monkeypatch.setattr(sms_client.settings, "sms_enabled", True)
    monkeypatch.setattr(sms_client.settings, "aliyun_sms_access_key_id", "test-id")
    monkeypatch.setattr(sms_client.settings, "aliyun_sms_access_key_secret", "test-secret")
    monkeypatch.setattr(sms_client.settings, "aliyun_sms_sign_name", "智枢测试")
    monkeypatch.setattr(sms_client.settings, "aliyun_sms_template_code", "SMS_123456")
    monkeypatch.setattr(sms_client.settings, "aliyun_sms_timeout_seconds", 2.8)


def test_disabled_sms_uses_mock_only_in_development(monkeypatch) -> None:
    monkeypatch.setattr(sms_client.settings, "app_env", "development")
    monkeypatch.setattr(sms_client.settings, "sms_enabled", False)
    monkeypatch.setattr(sms_client.settings, "aliyun_sms_access_key_id", "accidentally-set")

    assert sms_client.SmsClient().is_mock is True


def test_production_never_uses_mock(monkeypatch) -> None:
    monkeypatch.setattr(sms_client.settings, "app_env", "production")
    monkeypatch.setattr(sms_client.settings, "sms_enabled", False)

    with pytest.raises(RuntimeError, match="禁止 mock"):
        sms_client.SmsClient()


def test_enabled_sms_requires_all_credentials(monkeypatch) -> None:
    _configure_real(monkeypatch)
    monkeypatch.setattr(sms_client.settings, "aliyun_sms_template_code", "")

    with pytest.raises(RuntimeError, match="ALIYUN_SMS_TEMPLATE_CODE"):
        sms_client.SmsClient()


def test_real_send_signs_request_and_enforces_sub_three_second_timeout(monkeypatch) -> None:
    _configure_real(monkeypatch)
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"Code": "OK", "RequestId": "request-id"}

    def fake_get(url, *, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(sms_client.httpx, "get", fake_get)

    assert sms_client.SmsClient().send("13800138000", "123456") is True
    assert captured["url"] == sms_client.SMS_ENDPOINT
    assert captured["timeout"] == 2.8
    assert captured["params"]["PhoneNumbers"] == "13800138000"
    assert captured["params"]["TemplateParam"] == '{"code":"123456"}'
    assert captured["params"]["Signature"]
    assert "test-secret" not in captured["params"].values()


def test_gateway_rejection_returns_false(monkeypatch) -> None:
    _configure_real(monkeypatch)

    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"Code": "isv.SMS_SIGNATURE_ILLEGAL", "Message": "invalid"},
    )
    monkeypatch.setattr(sms_client.httpx, "get", lambda *args, **kwargs: response)

    assert sms_client.SmsClient().send("13800138000", "123456") is False


def test_unavailable_client_becomes_business_error_without_writing_code(
    monkeypatch, fake_redis
) -> None:
    """功能关闭/配置缺失应返回可理解的网关错误，且不消耗验证码状态。"""
    from app.services import auth_service

    def unavailable():
        raise RuntimeError("disabled")

    monkeypatch.setattr(sms_client, "get_sms_client", unavailable)

    with pytest.raises(auth_service.SmsDeliveryError, match="配置不完整"):
        auth_service.send_verification_code("13800139999", code_type="sms")

    assert fake_redis.get("vcode:sms:13800139999") is None
    assert fake_redis.get("vcode_freq:sms:13800139999") is None
