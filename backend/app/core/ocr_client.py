"""
backend/app/core/ocr_client.py

职责: 封装百度 OCR 文字识别（扫描件/图片型 PDF 文本提取失败时的兜底通道）

核心设计:
  - key 占位降级: settings.ocr_api_key 为空 -> mock 模式，recognize 返回固定占位文本，
    不调真实网关。用户后填真实 key 自动切真实模式（与 alipay_client/wechat_client 同构）。
  - 单例: get_ocr_client() 模块级缓存，避免每次请求重读 key。
  - 真实模式: API Key + Secret Key 换 access_token -> 调通用文字识别 high-precision 接口。
  - 只做"图片 -> 文本"单步识别，PDF 拆页由 file_parser 负责逐页转图后调本 client。

关键技术点:
  - 百度 OCR access_token 有效期 30 天，本 demo 每次调用都尝试刷新（简化，不缓存 token）。
    生产应缓存 token 并在过期前刷新。
  - 通用文字识别（高精度版）: rest/2.0/ocr/v1/accurate_basic，image 参数 base64 编码。
  - mock 模式返回非空文本: 保证 OCR 兜底后下游切片有内容可切，不产生空 chunk。

依赖关系:
  - 上游: app.utils.file_parser（PDF 提取失败时调 ocr_client.recognize）
  - 下游: app.config.settings、requests（真实模式才 import，mock 模式不依赖）
"""

import base64
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 百度 OCR 网关
_BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
_BAIDU_OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"

# mock 模式返回的占位文本（非空，保证下游切片有内容）
_MOCK_OCR_TEXT = "[OCR mock] 扫描件文字识别结果占位文本（未配置百度 OCR key，返回 mock 内容）。"


class OcrClient:
    """百度 OCR 客户端封装（mock 模式 + 真实模式双形态）。

    is_mock=True 时 recognize 返回固定占位文本，不触达真实网关；
    is_mock=False 时调百度 OCR API 做真实文字识别。
    """

    def __init__(self) -> None:
        # 模式判定: api_key 为空 -> mock 模式（开发/测试/未配 key 时降级）
        self.is_mock: bool = not settings.ocr_api_key
        self._api_key: str = settings.ocr_api_key
        self._secret_key: str = settings.ocr_secret_key

    # ── 文字识别 ──
    def recognize(self, image: bytes) -> str:
        """对单张图片做文字识别，返回识别出的纯文本。

        Args:
            image: 图片二进制内容（PNG/JPEG）

        Returns:
            str: 识别出的文本（多行用换行拼接）；识别失败返回空串
        """
        if self.is_mock:
            # mock 模式返回占位文本（非空，保证下游切片有内容）
            # closure-plan P1-1: mock 不再返回占位文本（否则会污染知识库）。
            # 未配置 OCR key 时返回空串，由 file_parser 向上抛"无法识别"错误，
            # 文档置 failed 而非把假内容入库。
            return ""

        # 真实模式
        try:
            return self._recognize_real(image)
        except Exception as e:
            logger.warning(f"OCR recognize failed, returning empty: {e}")
            return ""

    def recognize_pdf_page(self, page_image: bytes) -> str:
        """对 PDF 单页转出的图片做 OCR（语义别名，内部复用 recognize）。

        file_parser 将 PDF 每页渲染为图片后逐页调本方法，结果按页拼接。
        """
        return self.recognize(page_image)

    def _recognize_real(self, image: bytes) -> str:
        """真实模式: 换 token -> 调 OCR 接口 -> 拼接文字。

        百度 OCR 通用文字识别（高精度版）返回 words_result 列表，
        每项含 words 字段，拼接为多行文本返回。
        """
        import requests  # 真实模式才 import，mock 模式不依赖

        # 1. 换 access_token
        token = self._fetch_access_token()
        if not token:
            logger.warning("OCR access_token fetch failed")
            return ""

        # 2. 调文字识别接口（image 参数 base64 编码 + url encode）
        img_b64 = base64.b64encode(image).decode("utf-8")
        resp = requests.post(
            _BAIDU_OCR_URL,
            params={"access_token": token},
            data={"image": img_b64},
            timeout=15,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()

        # 错误码处理（百度返回 error_code 时识别失败）
        if "error_code" in data:
            logger.warning(f"OCR error: {data.get('error_code')} {data.get('error_msg')}")
            return ""

        # 拼接 words_result 中每行的 words
        words_list = data.get("words_result") or []
        return "\n".join(item.get("words", "") for item in words_list)

    def _fetch_access_token(self) -> str:
        """用 API Key + Secret Key 换百度 access_token。

        生产应缓存 token（有效期 30 天），本 demo 简化每次拉取。
        """
        import requests

        resp = requests.post(
            _BAIDU_TOKEN_URL,
            params={
                "grant_type": "client_credentials",
                "client_id": self._api_key,
                "client_secret": self._secret_key,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("access_token", "")


# 模块级单例缓存，避免每次请求重读 key
_client: OcrClient | None = None


def get_ocr_client() -> OcrClient:
    """获取 OcrClient 单例（首次调用时按 settings 初始化）。

    单例设计: 配置读取 + client 构造开销小，单例保持与 alipay/wechat client 同构。
    """
    global _client
    if _client is None:
        _client = OcrClient()
    return _client


def reset_ocr_client() -> None:
    """重置单例（测试用：monkeypatch settings 后重建 client）。"""
    global _client
    _client = None
