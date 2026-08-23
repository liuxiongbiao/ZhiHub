"""支付宝沙箱支付闭环只读预检（PRD §3.10）。

不访问支付页面、不产生真实付款，也不输出密钥或完整签名 URL。检查密钥解析、
RSA2 支付参数生成、支付开关和公网 HTTPS 回调前置条件。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.core.alipay_client import get_alipay_client, reset_alipay_client  # noqa: E402


def _public_https(url: str | None) -> bool:
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        )
    except (TypeError, ValueError):
        return False


def main() -> int:
    """执行不产生交易的支付宝配置预检，并用退出码区分代码与外部条件。"""

    configured = {
        "app_id": bool(settings.alipay_app_id),
        "app_private_key": bool(settings.alipay_app_private_key),
        "alipay_public_key": bool(settings.alipay_public_key),
    }
    reset_alipay_client()
    client = get_alipay_client()
    trade_no = f"ZH-PREFLIGHT-{int(time.time())}"
    payment_url = client.create_payment(trade_no, 0.01, "ZhiHub 支付宝沙箱预检")
    parsed = urlparse(payment_url)
    query = parse_qs(parsed.query)
    signed_url_ready = all(
        [
            query.get("method") == ["alipay.trade.page.pay"],
            query.get("sign_type") == ["RSA2"],
            bool(query.get("sign")),
            bool(query.get("biz_content")),
            query.get("app_id") == [settings.alipay_app_id],
        ]
    )
    sdk_initialized = getattr(client, "_alipay", None) is not None
    notify_ready = _public_https(settings.alipay_notify_url)
    code_ready = all(configured.values()) and not client.is_mock and sdk_initialized and signed_url_ready
    callback_ready = bool(settings.payment_enabled and notify_ready)
    overall = code_ready and callback_ready
    status = "ready" if overall else "external_prerequisite_missing" if code_ready else "code_or_key_error"

    print(
        json.dumps(
            {
                "status": status,
                "payment_enabled": settings.payment_enabled,
                "sandbox": settings.alipay_sandbox,
                "key_material_configured": configured,
                "client_real_mode": not client.is_mock,
                "sdk_initialized": sdk_initialized,
                "signed_page_pay_url_ready": signed_url_ready,
                "gateway_host": parsed.hostname,
                "notify_public_https": notify_ready,
                "ready_for_real_callback": overall,
                "next_action": (
                    "Use the sandbox buyer account to pay and verify the paid invoice plus duplicate callback idempotency."
                    if overall
                    else "Enable PAYMENT_ENABLED and configure a public HTTPS ALIPAY_NOTIFY_URL after the sandbox app/public key is confirmed."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if overall:
        return 0
    return 2 if code_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
