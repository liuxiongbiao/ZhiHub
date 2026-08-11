"""
backend/app/core/wechat_client.py

职责: 封装微信公众号 OAuth2 网页授权（生成授权 URL / code 换 openid）
分工: 🔴 强模型独立完成（OAuth 回调安全，openid 不可伪造）
对应日程: PRD 3.1 #12 微信扫码登录

核心设计:
  - key 占位降级: settings.wechat_app_id 为空 -> mock 模式，get_auth_url 返回假 URL、
    exchange_code_for_openid 返回 mock_openid_{code}。用户后填真实 key 自动切真实模式。
  - 单例: get_wechat_client() 模块级缓存，避免每次请求重读 key。
  - 真实模式用 httpx 调微信 OAuth2/access_token 接口换 openid。
  - openid 是微信用户在该公众号下的唯一标识，绑定后可作为登录凭证。

关键技术点:
  - OAuth2 网页授权流程: 引导用户跳转授权 URL -> 微信回调带 code -> 后端用 code 换 openid
  - snsapi_login scope: 微信扫码登录（PC 网站），需要公众号认证
  - code 一次性: 微信回调的 code 只能使用一次，5 分钟过期，不可重放
  - mock 模式: 开发/测试无真实公众号时降级，exchange 返回 mock_openid_{code}

依赖关系:
  - 上游: auth_service.wechat_login_start / wechat_login_callback / wechat_bind
  - 下游: app.config.settings、httpx（真实模式才 import，mock 模式不依赖）
"""

import logging
from urllib.parse import quote

from app.config import settings

logger = logging.getLogger(__name__)


class WeChatClient:
    """微信 OAuth2 客户端封装（mock 模式 + 真实模式双形态）。

    is_mock=True 时所有方法返回受控假结果，不触达真实微信网关；
    is_mock=False 时调用微信 API 走真实 OAuth2 流程。
    """

    def __init__(self) -> None:
        # 模式判定: app_id 为空 -> mock 模式（开发/测试/未配 key 时降级）
        self.is_mock: bool = not settings.wechat_app_id

    # ── 生成授权 URL ──
    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """生成微信 OAuth2 网页授权 URL，引导用户扫码。

        Args:
            redirect_uri: 授权后回调地址（需在公众号后台配置可信域名）
            state: 防 CSRF 随机串，回调时原样返回供校验

        Returns:
            str: 微信授权页 URL（前端 window.location 跳转）
        """
        if self.is_mock:
            # mock 模式返回假 URL，含 redirect_uri 与 state 便于断言
            return (
                "https://open.weixin.qq.com/connect/oauth2/authorize"
                f"?mock=1&redirect_uri={redirect_uri}&state={state}"
            )

        # 真实模式: 拼装标准微信 OAuth2 授权 URL
        # snsapi_login = 扫码登录（PC 网站），response_type=code 是 OAuth2 标准
        return (
            "https://open.weixin.qq.com/connect/oauth2/authorize"
            f"?appid={settings.wechat_app_id}"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            "&response_type=code"
            "&scope=snsapi_login"
            f"&state={state}"
            "#wechat_redirect"
        )

    # ── code 换 openid ──
    def exchange_code_for_openid(self, code: str) -> str:
        """用微信回调的 code 换取用户 openid。

        流程: POST sns/oauth2/access_token -> 返回 openid + access_token（本场景只需 openid）。
        code 一次性，5 分钟过期，不可重放。

        Args:
            code: 微信回调携带的授权 code

        Returns:
            str: 用户在该公众号下的唯一 openid

        Raises:
            RuntimeError: 微信接口返回 errcode 非 0（code 无效/过期/AppSecret 错误）
        """
        if self.is_mock:
            # mock 模式: 返回 mock_openid_{code}，不调真实网关
            return f"mock_openid_{code}"

        # 真实模式: 调微信 OAuth2/access_token 接口
        try:
            import httpx
        except ImportError as e:  # httpx 未安装 -> 降级 mock 并告警
            logger.warning(f"httpx 未安装，降级 mock 模式: {e}")
            return f"mock_openid_{code}"

        try:
            resp = httpx.get(
                "https://api.weixin.qq.com/sns/oauth2/access_token",
                params={
                    "appid": settings.wechat_app_id,
                    "secret": settings.wechat_app_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                timeout=10,
            )
            data = resp.json()
        except Exception as e:
            logger.error(f"WeChat exchange_code failed (code={code}): {e}")
            raise RuntimeError(f"微信授权失败: {e}") from e

        # 微信接口错误时返回 errcode + errmsg（而非 openid）
        if "errcode" in data and data["errcode"] != 0:
            raise RuntimeError(f"微信授权失败: {data.get('errmsg', 'unknown error')}")

        openid = data.get("openid")
        if not openid:
            raise RuntimeError("微信授权失败: 响应缺少 openid")
        return openid


# 模块级单例缓存，避免每次请求重读 settings
_client: WeChatClient | None = None


def get_wechat_client() -> WeChatClient:
    """获取 WeChatClient 单例（首次调用时按 settings 初始化）。

    单例设计: 配置读取与模式判定开销虽小，单例保证全局一致的 client 实例，
    且便于测试通过 monkeypatch 类方法控制行为。
    """
    global _client
    if _client is None:
        _client = WeChatClient()
    return _client


def reset_wechat_client() -> None:
    """重置单例（测试用：monkeypatch settings 后重建 client）。"""
    global _client
    _client = None
