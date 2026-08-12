"""
backend/tests/contracts/test_ocr.py

职责: 测试 #19 OCR 兜底（百度 OCR mock 模式 + PDF 提取失败兜底）契约

测试内容:
  - TestOcrClient: mock 模式返回非空文本 / 单例重置 / PDF 页识别别名
  - TestOcrFallback: PDF 无文本层走 OCR 兜底 / 解析异常走 OCR / OCR 失败返回空
"""

import io

import pytest

from app.core.ocr_client import get_ocr_client, reset_ocr_client


class TestOcrClient:
    """百度 OCR 客户端 mock 模式契约。"""

    def test_mock_mode_is_mock(self):
        """config 未配 key -> is_mock=True。"""
        reset_ocr_client()
        client = get_ocr_client()
        assert client.is_mock is True

    def test_mock_recognize_returns_empty(self):
        """mock 模式 recognize 返回空串（不允许占位文本伪装识别结果污染知识库，closure-plan P1-1）。"""
        reset_ocr_client()
        client = get_ocr_client()
        text = client.recognize(b"fake-image-bytes")
        assert text == ""

    def test_recognize_pdf_page_alias(self):
        """recognize_pdf_page 复用 recognize，返回相同结果。"""
        reset_ocr_client()
        client = get_ocr_client()
        r1 = client.recognize(b"img")
        r2 = client.recognize_pdf_page(b"img")
        assert r1 == r2

    def test_reset_client_rebuilds(self):
        """reset_ocr_client 后单例重建。"""
        c1 = get_ocr_client()
        reset_ocr_client()
        c2 = get_ocr_client()
        assert c1 is not c2


class TestOcrFallback:
    """PDF 文本提取失败时 OCR 兜底契约。"""

    def test_pdf_empty_text_raises_without_ocr_key(self, monkeypatch):
        """PDF 无文本层 + 未配置 OCR key -> 报错而非入库假内容（closure-plan P1-1）。"""

        # mock PdfReader: pages[0].extract_text() 返回空串
        class _FakePage:
            def extract_text(self):
                return ""

        class _FakeReader:
            def __init__(self, stream):
                self.pages = [_FakePage()]

        monkeypatch.setattr("PyPDF2.PdfReader", _FakeReader)
        reset_ocr_client()

        from app.utils.file_parser import extract_text, FileParseError

        with pytest.raises(FileParseError):
            extract_text("scan.pdf", b"%PDF-1.4 fake")

    def test_pdf_parse_error_raises_without_ocr_key(self, monkeypatch):
        """PDF 解析抛异常 + 未配置 OCR key -> 报错而非入库假内容。"""

        class _BrokenReader:
            def __init__(self, stream):
                raise RuntimeError("corrupted pdf")

        monkeypatch.setattr("PyPDF2.PdfReader", _BrokenReader)
        reset_ocr_client()

        from app.utils.file_parser import extract_text, FileParseError

        with pytest.raises(FileParseError):
            extract_text("broken.pdf", b"%PDF-1.4 broken")

    def test_ocr_disabled_raises_error(self, monkeypatch):
        """OCR 也失败（返回空）-> extract_text 抛 FileParseError。"""

        class _FakePage:
            def extract_text(self):
                return ""

        class _FakeReader:
            def __init__(self, stream):
                self.pages = [_FakePage()]

        monkeypatch.setattr("PyPDF2.PdfReader", _FakeReader)

        # mock OCR 使其返回空串（模拟 OCR 失败）
        from app.core import ocr_client

        class _NoTextOcrClient(ocr_client.OcrClient):
            def recognize(self, image):
                return ""

        monkeypatch.setattr(ocr_client, "get_ocr_client", lambda: _NoTextOcrClient())
        reset_ocr_client()

        from app.utils.file_parser import extract_text, FileParseError

        with pytest.raises(FileParseError):
            extract_text("empty.pdf", b"%PDF-1.4 empty")
