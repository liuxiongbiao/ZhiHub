"""
backend/app/utils/file_parser.py

职责: 文件类型检测、大小校验、轻量文档切片（简化替代 RagFlow）
对应日程: 002-knowledge-rag

核心设计:
  切片规则: Markdown 按 ## 标题分段，标题作为上下文前缀；
            PDF/Word 先提取纯文本，按双换行分段，再按 512 字符做二级切分。
  chunk_size ≈ 512 tokens（约 300-400 中文字符），chunk_overlap ≈ 128 tokens。
"""

import io
import re
from dataclasses import dataclass

# 支持的文件格式白名单
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md"}
# 文件大小上限：10 MB
MAX_FILE_SIZE = 10 * 1024 * 1024
# 解压后文本上限（T32 压缩炸弹防护）: 10MB 原始文件限制不覆盖解压后膨胀
# （docx zip/PDF 解压后可放大数倍），提取后统一校验
MAX_TEXT_LENGTH = 5 * 1024 * 1024
# 切片参数
CHUNK_SIZE_CHARS = 512     # ~512 tokens ≈ 300-400 中文字符
CHUNK_OVERLAP_CHARS = 128  # ~128 tokens ≈ 80-100 中文字符


@dataclass
class Chunk:
    """文档切片."""

    index: int
    text: str
    heading: str | None = None  # 所属标题（Markdown 场景）


class FileValidationError(ValueError):
    """文件校验失败."""


class FileParseError(ValueError):
    """文件解析失败."""


# ---------- 文件校验 ----------
def validate_file(filename: str, content: bytes) -> None:
    """校验文件格式和大小，不通过抛 FileValidationError."""
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            f"不支持的文件格式: {ext}，仅支持 {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if len(content) > MAX_FILE_SIZE:
        size_mb = len(content) / (1024 * 1024)
        raise FileValidationError(f"文件大小超过限制: {size_mb:.1f}MB (上限 10MB)")
    if len(content) == 0:
        raise FileValidationError("文件为空")


def _get_extension(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


# ---------- 文本提取 ----------
def extract_text(filename: str, content: bytes) -> str:
    """根据文件类型提取纯文本（提取后统一校验文本长度，防解压膨胀）。"""
    ext = _get_extension(filename)

    if ext == ".md":
        text = content.decode("utf-8")
    elif ext == ".txt":
        text = content.decode("utf-8")
    elif ext == ".pdf":
        text = _extract_pdf_text(content)
    elif ext == ".docx":
        text = _extract_docx_text(content)
    else:
        raise FileParseError(f"不支持的文件格式: {ext}")

    # T32: 解压后文本长度校验（压缩炸弹防护，原始 10MB 限制不覆盖解压膨胀）
    if len(text) > MAX_TEXT_LENGTH:
        raise FileValidationError(f"文档解压后文本超过上限（{MAX_TEXT_LENGTH // (1024 * 1024)}MB）")
    return text


def _extract_pdf_text(content: bytes) -> str:
    """从 PDF 文件提取文本.

    使用 PyPDF2 提取文本（轻量，不依赖重型 OCR）。
    提取失败（扫描件/图片型 PDF 无文本层）时走 OCR 兜底（019）。
    """
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        result = "\n\n".join(pages)
        if not result.strip():
            # 无文本层 -> OCR 兜底（019）: 扫描件/图片型 PDF 需 OCR 才能拿到文字
            ocr_text = _ocr_fallback(content)
            if ocr_text:
                return ocr_text
            raise FileParseError("PDF 文件中未提取到文本内容（可能是扫描件或图片型 PDF）")
        return result
    except ImportError:
        # PyPDF2 未安装 -> 尝试 OCR 兜底，仍无结果则报错
        ocr_text = _ocr_fallback(content)
        if ocr_text:
            return ocr_text
        raise FileParseError("PDF 解析库未安装 (PyPDF2)") from None
    except FileParseError:
        raise
    except Exception as e:
        # PyPDF2 解析异常（损坏文件等）-> 尝试 OCR 兜底，仍失败则报错
        ocr_text = _ocr_fallback(content)
        if ocr_text:
            return ocr_text
        raise FileParseError(f"PDF 解析失败: {e}") from e


def _ocr_fallback(content: bytes) -> str:
    """OCR 兜底: 调 ocr_client 对内容做文字识别（019）。

    百度 OCR 接受图片，PDF 需先转图片。本 demo 简化: 直接把二进制交给 ocr_client，
    mock 模式返回占位文本；真实模式需在此处加 PDF->image 渲染（pdf2image）。
    返回空串表示兜底失败，调用方应继续报错。

    设计决策:
      - 兜底失败不抛异常: 让上层决定是否降级，本函数只返回结果
      - 真实 PDF 转图依赖 pdf2image + poppler: 生产环境按需启用，测试用 mock 即可
    """
    try:
        from app.core.ocr_client import get_ocr_client

        client = get_ocr_client()
        # mock 模式直接返回占位文本；真实模式此处应逐页 pdf2image.convert_from_bytes
        # 再调 client.recognize_pdf_page，本 demo 简化为整体识别
        if client.is_mock:
            return client.recognize(content)
        # 真实模式: PDF -> 图片逐页 OCR（依赖 pdf2image，未安装则降级返回空）
        try:
            from pdf2image import convert_from_bytes

            images = convert_from_bytes(content)
            texts = []
            for img in images:
                import io as _io
                buf = _io.BytesIO()
                img.save(buf, format="PNG")
                page_text = client.recognize_pdf_page(buf.getvalue())
                if page_text:
                    texts.append(page_text)
            return "\n\n".join(texts)
        except ImportError:
            logger_ = __import__("logging").getLogger(__name__)
            logger_.warning("pdf2image 未安装，真实模式 PDF OCR 降级失败")
            return ""
    except Exception:
        return ""


def _extract_docx_text(content: bytes) -> str:
    """从 Word 文档提取文本."""
    try:
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        result = "\n\n".join(paragraphs)
        if not result.strip():
            raise FileParseError("Word 文档中未提取到文本内容")
        return result
    except ImportError:
        raise FileParseError("Word 解析库未安装 (python-docx)") from None


# ---------- 文本切片 ----------
def chunk_text(text: str, filename: str) -> list[Chunk]:
    """将文本按段落+标题规则切片.

    Markdown 文件：按 ## 标题分段。
    其他文件：按双换行分段，再按 CHUNK_SIZE_CHARS 做二级切分。
    """
    if filename.endswith(".md"):
        return _chunk_markdown(text)
    else:
        return _chunk_generic(text)


def _chunk_markdown(text: str) -> list[Chunk]:
    """Markdown 切片：按 ## 标题分段，标题作为该段的上下文前缀."""
    # 按 ## 标题分割（保留标题行）
    sections = re.split(r"\n(?=## )", text)
    chunks: list[Chunk] = []
    idx = 0

    for section in sections:
        # 取第一行作为 heading
        lines = section.strip().split("\n", 1)
        heading = lines[0].lstrip("# ") if lines else None
        body = lines[1] if len(lines) > 1 else ""

        if not body.strip():
            continue

        # 如果段落过长，做二级切分
        if len(body) > CHUNK_SIZE_CHARS:
            sub_chunks = _split_by_size(body, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS)
            for sub in sub_chunks:
                chunks.append(Chunk(index=idx, text=sub, heading=heading))
                idx += 1
        else:
            chunks.append(Chunk(index=idx, text=body, heading=heading))
            idx += 1

    return chunks


def _chunk_generic(text: str) -> list[Chunk]:
    """通用文本切片：按双换行分段，二级按字符数切分."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[Chunk] = []
    idx = 0

    for para in paragraphs:
        if len(para) > CHUNK_SIZE_CHARS:
            sub_chunks = _split_by_size(para, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS)
            for sub in sub_chunks:
                chunks.append(Chunk(index=idx, text=sub))
                idx += 1
        else:
            chunks.append(Chunk(index=idx, text=para))
            idx += 1

    return chunks


def _split_by_size(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按固定字符数滑动窗口切分."""
    if len(text) <= chunk_size:
        return [text]

    result = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        result.append(text[start:end])
        start += chunk_size - overlap
    return result


def parse_and_chunk(filename: str, content: bytes) -> list[Chunk]:
    """一站式：校验 -> 提取 -> 切片."""
    validate_file(filename, content)
    text = extract_text(filename, content)
    return chunk_text(text, filename)
