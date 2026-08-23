"""阿里云短信客户端（PRD 3.1.2 手机验证码发送）。

与 wechat_client/alipay_client 同构的 mock/真实双模式：
  - 未配置 ALIYUN_SMS_ACCESS_KEY_ID 时走 mock（日志输出验证码，开发/测试零配置）；
  - 配置密钥后走阿里云 RPC 风格 SendSms 接口（HMAC-SHA1 签名），生产环境强制要求配置。
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
        # 生产环境禁止 mock：短信是注册/登录核心链路，未配置密钥应显式失败而非静默占位
        if settings.app_env not in ("development", "test") and not settings.aliyun_sms_access_key_id:
            raise RuntimeError("非 development 环境必须配置 ALIYUN_SMS_ACCESS_KEY_ID（禁止 mock 短信）")
        self.is_mock: bool = not settings.aliyun_sms_access_key_id

    def send(self, phone: str, code: str) -> bool:
        """发送验证码短信。mock 模式直接返回 True；真实模式调用阿里云 SendSms。"""
        if self.is_mock:
            logger.info(f"[mock sms] send verification code to {phone}: {code}")
            return True

        params = {
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

        # RPC 签名: 参数按字典序排序 → 规范化 → HMAC-SHA1(Secret&, StringToSign)
        canonical = "&".join(
            f"{k}={_percent_encode(params[k])}" for k in sorted(params)
        )
        string_to_sign = "GET&%2F&" + _percent_encode(canonical)
        digest = hmac.new(
            (settings.aliyun_sms_access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        params["Signature"] = base64.b64encode(digest).decode("ascii")

        try:
            resp = httpx.get(SMS_ENDPOINT, params=params, timeout=10.0)
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
