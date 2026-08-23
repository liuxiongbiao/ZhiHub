"""真实阿里云短信验收：发送一次随机验证码并校验网关调用耗时小于 3 秒。

用法（先通过环境变量注入 SMS_ENABLED=true 与四项 ALIYUN_SMS_* 凭据）：
    python scripts/verify_sms.py --phone 13800138000

脚本拒绝 mock 模式，不输出验证码或凭据，只输出网关结果与耗时。
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.sms_client import get_sms_client  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="验证阿里云短信真实发送与 3 秒 SLA")
    parser.add_argument("--phone", required=True, help="接收验证码的中国大陆手机号")
    args = parser.parse_args()

    client = get_sms_client()
    if client.is_mock:
        print("FAIL: 当前为 mock 模式；请配置 SMS_ENABLED=true 与四项 ALIYUN_SMS_* 凭据。")
        return 2

    code = f"{secrets.randbelow(1_000_000):06d}"
    started = time.perf_counter()
    delivered = client.send(args.phone, code)
    elapsed_ms = (time.perf_counter() - started) * 1000

    if not delivered:
        print(f"FAIL: 阿里云网关拒绝或请求失败，耗时 {elapsed_ms:.1f}ms。")
        return 1
    if elapsed_ms >= 3000:
        print(f"FAIL: 网关返回成功，但耗时 {elapsed_ms:.1f}ms，未达到 <3000ms。")
        return 1
    print(f"PASS: 阿里云网关返回成功，耗时 {elapsed_ms:.1f}ms；请在手机上确认收码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
