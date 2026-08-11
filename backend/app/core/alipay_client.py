"""
backend/app/core/alipay_client.py

职责: 封装支付宝沙箱支付网关（下单生成支付链接 / 异步回调验签 / 退款）
分工: 🔴 强模型独立完成（签名校验错=可被伪造回调伪造支付）
对应日程: 010 业务闭环 / PRD 3.10

核心设计:
  - key 占位降级: settings.alipay_app_id 为空 -> mock 模式，create_payment 返回假链接、
    verify_callback 直接返回 True、refund 直接返回 True。用户后填真实 key 自动切真实模式。
  - 单例: get_alipay_client() 模块级缓存，避免每次请求重读 key 解析证书。
  - 真实模式用 python-alipay-sdk: AliPay 对象封装签名/验签/网关请求。
  - 验签是资金安全核心: 回调必须验签，否则攻击者可伪造 TRADE_SUCCESS 篡改账单状态。

关键技术点:
  - python-alipay-sdk 的 AliPay(alipay_public_key, app_private_key, sign_type="RSA2")
  - api_alipay_trade_page_pay: 电脑网站支付，返回跳转 URL
  - verify_callback: SDK 的 sign_verify 验签支付宝异步通知签名
  - refund: api_alipay_trade_refund 发起退款

依赖关系:
  - 上游: billing_service（create_payment/handle_payment_callback）、order_service（refund）
  - 下游: app.config.settings、python-alipay-sdk（真实模式才 import，mock 模式不依赖）
"""

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class AlipayClient:
    """支付宝客户端封装（mock 模式 + 真实模式双形态）。

    is_mock=True 时所有方法返回受控假结果，不触达真实网关；
    is_mock=False 时调用 python-alipay-sdk 走真实沙箱。
    """

    def __init__(self) -> None:
        # T33: 生产环境禁止 mock——忘配 key 时 mock 验签恒 True，可伪造支付回调篡改账单
        if settings.app_env != "development" and not settings.alipay_app_id:
            raise RuntimeError(
                "非 development 环境必须配置 ALIPAY_APP_ID（禁止 mock 模式：mock 验签恒 True 可伪造支付回调）"
            )
        # 模式判定: app_id 为空 -> mock 模式（开发/测试/未配 key 时降级）
        # is_mock 仅由 app_id 是否为空决定，真实 key 解析失败不改变模式判定
        # （真实模式但 _alipay=None 时方法降级返回 mock 结果，详见各方法）
        self.is_mock: bool = not settings.alipay_app_id
        self._alipay: Any = None
        if not self.is_mock:
            self._init_real()

    def _init_real(self) -> None:
        """真实模式初始化 AliPay 对象（懒加载 SDK）。

        key 解析失败不抛异常，仅置 _alipay=None，方法调用时降级返回 mock 结果，
        保证应用启动不被非法 key 阻断。
        """
        try:
            from alipay import AliPay
        except ImportError as e:  # SDK 未安装 -> 降级 mock 并告警
            logger.warning(f"python-alipay-sdk 未安装，降级 mock 模式: {e}")
            return

        try:
            self._alipay = AliPay(
                appid=settings.alipay_app_id,
                app_notify_url=settings.alipay_notify_url or None,
                app_private_key_string=settings.alipay_app_private_key,
                alipay_public_key_string=settings.alipay_public_key,
                sign_type="RSA2",
                debug=settings.alipay_sandbox,  # 沙箱 debug=True
            )
            logger.info(f"AlipayClient initialized (sandbox={settings.alipay_sandbox}, appid={settings.alipay_app_id})")
        except Exception as e:
            # key 格式不合法（PEM 解析失败等）-> _alipay 保持 None，方法降级
            logger.warning(f"AlipayClient key 解析失败，方法将降级 mock: {e}")

    # ── 下单生成支付链接 ──
    def create_payment(self, trade_no: str, amount: float, subject: str) -> str:
        """生成电脑网站支付跳转 URL。

        Args:
            trade_no: 商户订单号（对应账单 payment_trade_no）
            amount: 支付金额（元）
            subject: 订单标题

        Returns:
            str: 支付页跳转 URL
        """
        if self.is_mock:
            # mock 模式返回沙箱网关假链接，含 trade_no 便于断言
            return (
                f"{settings.alipay_gateway}?mock=1"
                f"&trade_no={trade_no}&amount={amount}&subject={subject}"
            )

        # 真实模式但 key 解析失败 -> 降级 mock 结果（不阻断业务）
        if self._alipay is None:
            return (
                f"{settings.alipay_gateway}?degraded=1"
                f"&trade_no={trade_no}&amount={amount}&subject={subject}"
            )

        order = {
            "out_trade_no": trade_no,
            "total_amount": str(amount),
            "subject": subject,
            "product_code": "FAST_INSTANT_TRADE_PAY",
        }
        url = self._alipay.api_alipay_trade_page_pay(
            biz_content=order,
            return_url="",
            notify_url=settings.alipay_notify_url or None,
        )
        return f"{settings.alipay_gateway}?{url}"

    # ── 异步回调验签 ──
    def verify_callback(self, form_data: dict) -> bool:
        """校验支付宝异步通知签名。

        资金安全核心: 不验签或弱验签 = 攻击者可伪造支付成功。

        Args:
            form_data: 支付宝 POST 过来的表单字段（含 sign/sign_type）

        Returns:
            bool: True 签名合法，False 签名不匹配
        """
        if self.is_mock:
            # mock 模式默认验签通过（仅开发/测试，生产必须配真实 key）
            return True

        # 真实模式但 key 解析失败 -> 拒绝验签（密钥异常时绝不放行，防伪造回调）
        if self._alipay is None:
            logger.error("Alipay 真实模式密钥未加载，拒绝回调验签")
            return False

        # SDK 验签：data 为去 sign 后的其余字段，sign 单独传
        sign = form_data.get("sign")
        if not sign:
            return False
        data = {k: v for k, v in form_data.items() if k != "sign" and k != "sign_type"}
        try:
            return bool(self._alipay.verify(data, sign))
        except Exception as e:
            logger.warning(f"Alipay callback verify failed: {e}")
            return False

    # ── 退款 ──
    def refund(self, trade_no: str, amount: float) -> bool:
        """发起支付宝退款。

        Args:
            trade_no: 原支付商户订单号
            amount: 退款金额

        Returns:
            bool: True 退款受理成功
        """
        if self.is_mock:
            return True

        # 真实模式但 key 解析失败 -> 拒绝退款（密钥异常时绝不放款）
        if self._alipay is None:
            logger.error("Alipay 真实模式密钥未加载，拒绝退款")
            return False

        try:
            resp = self._alipay.api_alipay_trade_refund(
                biz_content={
                    "out_trade_no": trade_no,
                    "refund_amount": str(amount),
                }
            )
            # code=10000 表示成功
            return resp.get("code") == "10000"
        except Exception as e:
            logger.error(f"Alipay refund failed (trade_no={trade_no}): {e}")
            return False


# 模块级单例缓存，避免每次请求重读 key 解析证书
_client: AlipayClient | None = None


def get_alipay_client() -> AlipayClient:
    """获取 AlipayClient 单例（首次调用时按 settings 初始化）。

    单例设计: key 解析 + AliPay 对象构造开销不可忽略，单例避免重复；
    配置变更需重启进程（与 settings 全局单例语义一致）。
    """
    global _client
    if _client is None:
        _client = AlipayClient()
    return _client


def reset_alipay_client() -> None:
    """重置单例（测试用：monkeypatch settings 后重建 client）。"""
    global _client
    _client = None
