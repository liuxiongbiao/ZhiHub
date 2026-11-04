"""阿里云短信客户端（PRD 3.1.2 手机验证码发送）。

与 wechat_client/alipay_client 同构的 mock/真实双模式：
  - development/test 且 SMS_ENABLED=false 时走 mock（开发/测试零配置）；
  - SMS_ENABLED=true 且四项凭据齐全时走阿里云 RPC SendSms（HMAC-SHA1）；
  - production 禁止 mock，功能关闭或缺配置均显式失败。
"""

import base64
import hashlib
import hmac
import logging
import secrets
import urllib.parse
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SMS_ENDPOINT = "https://dysmsapi.aliyuncs.com/"


def _percent_encode(value: str) -> str:
    """RFC3986 百分号编码（Aliyun RPC 签名规范）。"""
    return urllib.parse.quote(str(value), safe="-_.~")


class SmsClient:
    """阿里云短信客户端（mock 模式 + 真实模式双形态）。"""

    def __init__(self) -> None:
        # 开关是唯一模式选择来源，避免“仅填了 key、未启用功能”时意外真实发送。
        if not settings.sms_enabled:
            if settings.app_env not in ("development", "test"):
                raise RuntimeError("生产环境短信功能未启用（SMS_ENABLED=false），禁止 mock 短信")
            self.is_mock = True
            return

        required = {
            "ALIYUN_SMS_ACCESS_KEY_ID": settings.aliyun_sms_access_key_id,
            "ALIYUN_SMS_ACCESS_KEY_SECRET": settings.aliyun_sms_access_key_secret,
            "ALIYUN_SMS_SIGN_NAME": settings.aliyun_sms_sign_name,
            "ALIYUN_SMS_TEMPLATE_CODE": settings.aliyun_sms_template_code,
        }
        missing = [key for key, value in required.items() if not str(value).strip()]
        if missing:
            raise RuntimeError("短信功能缺少配置：" + ", ".join(missing))
        self.is_mock = False

    def _sms_params(self, phone: str, code: str) -> dict:
        return {
            "AccessKeyId": settings.aliyun_sms_access_key_id,
            "Action": "SendSms",
            "Format": "JSON",
            "PhoneNumbers": phone,
            "RegionId": "cn-hangzhou",
            "SignName": settings.aliyun_sms_sign_name,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": secrets.token_hex(16),
            "SignatureVersion": "1.0",
            "TemplateCode": settings.aliyun_sms_template_code,
            "TemplateParam": '{"code":"%s"}' % code,
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": "2017-05-25",
        }

    def _sign_sms_params(self, params: dict) -> dict:
        canonical = "&".join(
            f"{key}={_percent_encode(params[key])}" for key in sorted(params)
        )
        string_to_sign = "GET&%2F&" + _percent_encode(canonical)
        digest = hmac.new(
            (settings.aliyun_sms_access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        params["Signature"] = base64.b64encode(digest).decode("ascii")
        return params

    def send(self, phone: str, code: str) -> bool:
        """发送验证码短信。mock 模式直接返回 True；真实模式调用阿里云 SendSms。"""
        if self.is_mock:
            logger.info(f"[mock sms] send verification code to {phone}: {code}")
            return True

        params = self._sign_sms_params(self._sms_params(phone, code))
        try:
            # PRD 验收要求真实短信发送调用 <3s；超时立即失败且不写入验证码/限频状态。
            resp = httpx.get(
                SMS_ENDPOINT,
                params=params,
                timeout=settings.aliyun_sms_timeout_seconds,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            logger.error(f"Aliyun SMS request failed: {e}")
            return False

        if body.get("Code") != "OK":
            logger.error(f"Aliyun SMS rejected: {body}")
            return False
        logger.info(f"Aliyun SMS sent to {phone} (template={settings.aliyun_sms_template_code})")
        return True


_sms_client: SmsClient | None = None


def get_sms_client() -> SmsClient:
    """进程级单例（与 wechat/alipay 客户端一致）。"""
    global _sms_client
    if _sms_client is None:
        _sms_client = SmsClient()
    return _sms_client
