"""
backend/app/utils/file_parser.py

职责: 文件类型检测、大小校验、轻量语义切片（RagFlow 不可用时的本地降级）

核心设计:
  切片规则: Markdown 按 ## 标题识别语义单元，PDF/Word 按段落识别语义单元；
            相邻短单元合并为 500-800 字的完整切片，长单元优先在句末切分。
  目标长度 700 字，最大 800 字；仅长段落滑窗切分时保留 80 字重叠。
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass

# 支持的文件格式白名单
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".xlsx"}
# 文件大小上限：由配置驱动（默认 10MB；产品确认后仅改 UPLOAD_MAX_FILE_SIZE_MB）
def _max_file_size() -> int:
    from app.config import settings
    return settings.upload_max_file_size_mb * 1024 * 1024
# 解压后文本上限（ 压缩炸弹防护）: 10MB 原始文件限制不覆盖解压后膨胀
# （docx zip/PDF 解压后可放大数倍），提取后统一校验
MAX_TEXT_LENGTH = 5 * 1024 * 1024
# 本地降级切片以 500-800 字为主，兼顾检索粒度与大文档入库吞吐（PRD §3.2）。
CHUNK_TARGET_CHARS = 700
CHUNK_MIN_CHARS = 500
CHUNK_MAX_CHARS = 800
CHUNK_OVERLAP_CHARS = 80


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


# ────────── 文件校验 ──────────
def validate_file(filename: str, content: bytes) -> None:
    """校验文件格式和大小，不通过抛 FileValidationError."""
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            f"不支持的文件格式: {ext}，仅支持 {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    max_bytes = _max_file_size()
    if len(content) > max_bytes:
        size_mb = len(content) / (1024 * 1024)
        raise FileValidationError(f"文件大小超过限制: {size_mb:.1f}MB (上限 {max_bytes // (1024 * 1024)}MB)")
    if len(content) == 0:
        raise FileValidationError("文件为空")


def _get_extension(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


# ────────── 文本提取 ──────────
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
    elif ext == ".xlsx":
        text = _extract_xlsx_text(content)
    else:
        raise FileParseError(f"不支持的文件格式: {ext}")

    # 解压后文本长度校验（压缩炸弹防护，原始 10MB 限制不覆盖解压膨胀）
    if len(text) > MAX_TEXT_LENGTH:
        raise FileValidationError(f"文档解压后文本超过上限（{MAX_TEXT_LENGTH // (1024 * 1024)}MB）")
    return text


def _extract_xlsx_text(content: bytes) -> str:
    """Extract text from .xlsx cells (PRD 3.2 spreadsheet support).

    Standard-library zipfile + XML parser (no openpyxl dependency):
    reads sharedStrings then iterates the first worksheet, joining cells by tab.
    Returns empty string on parse errors (upper layer empty-file check catches it).
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = z.namelist()
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                    shared.append("".join(t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
            sheet_name = next(
                (n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")), None,
            )
            if not sheet_name:
                return ""
            root = ET.fromstring(z.read(sheet_name))
            rows: list[str] = []
            for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
                cells: list[str] = []
                for c in row.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                    v = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                    if c.get("t") == "s" and v is not None and v.text:
                        idx = int(v.text)
                        if 0 <= idx < len(shared):
                            cells.append(shared[idx])
                    elif v is not None and v.text:
                        cells.append(v.text)
                if cells:
                    rows.append("\t".join(cells))
            return "\n".join(rows)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError):
        return ""

def _extract_pdf_text(content: bytes) -> str:
    """从 PDF 文件提取文本.

    使用 pypdf 提取文本（轻量，不依赖重型 OCR）。
    提取失败（扫描件/图片型 PDF 无文本层）时走 OCR 兜底。
    """
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader  # backward compat for venvs without pypdf

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        result = "\n\n".join(pages)
        if not result.strip():
            # 无文本层 → OCR 兜底: 扫描件/图片型 PDF 需 OCR 才能拿到文字
            ocr_text = _ocr_fallback(content)
            if ocr_text:
                return ocr_text
            raise FileParseError("PDF 文件中未提取到文本内容（可能是扫描件或图片型 PDF）")
        return result
    except ImportError:
        # PyPDF2 未安装 → 尝试 OCR 兜底，仍无结果则报错
        ocr_text = _ocr_fallback(content)
        if ocr_text:
            return ocr_text
        raise FileParseError("PDF 解析库未安装 (pypdf/PyPDF2)") from None
    except FileParseError:
        raise
    except Exception as e:
        # PyPDF2 解析异常（损坏文件等）→ 尝试 OCR 兜底，仍失败则报错
        ocr_text = _ocr_fallback(content)
        if ocr_text:
            return ocr_text
        raise FileParseError(f"PDF 解析失败: {e}") from e


def _ocr_fallback(content: bytes) -> str:
    """OCR 兜底: 调 ocr_client 对内容做文字识别。

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
        # 真实模式: PDF → 图片逐页 OCR（依赖 pdf2image，未安装则降级返回空）
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


# ────────── 文本切片 ──────────
def chunk_text(text: str, filename: str) -> list[Chunk]:
    """按标题或段落识别语义单元，再合并为 500-800 字切片。

    Markdown 文件按二级标题识别单元，其他文件按空行识别段落；相邻短单元会合并，
    避免大文档因大量短小标题产生数万条碎片并拖垮向量化管道。
    """
    if filename.endswith(".md"):
        return _chunk_markdown(text)
    else:
        return _chunk_generic(text)


def _chunk_markdown(text: str) -> list[Chunk]:
    """识别 Markdown 标题单元，并合并相邻短小节以保留语义上下文。"""
    sections = re.split(r"\n(?=## )", text)
    units: list[tuple[str | None, str]] = []

    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.split("\n", 1)
        if lines[0].startswith("## "):
            heading = lines[0].lstrip("# ").strip()
            body = lines[1].strip() if len(lines) > 1 else ""
        else:
            heading = None
            body = section
        if body:
            units.append((heading, body))

    return _merge_semantic_units(units)


def _chunk_generic(text: str) -> list[Chunk]:
    """识别通用文档段落，并合并相邻短段以减少碎片。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return _merge_semantic_units([(None, paragraph) for paragraph in paragraphs])


def _merge_semantic_units(units: list[tuple[str | None, str]]) -> list[Chunk]:
    """把相邻标题/段落单元合并为接近 700 字的语义完整切片。"""
    pending_text = ""
    pending_heading: str | None = None
    merged: list[tuple[str, str | None]] = []

    def flush_pending() -> None:
        """提交当前缓冲区；缓冲只在语义单元边界处刷新。"""
        nonlocal pending_text, pending_heading
        if pending_text:
            merged.append((pending_text, pending_heading))
        pending_text = ""
        pending_heading = None

    for heading, body in units:
        body = body.strip()
        if not body:
            continue
        heading_prefix = f"{heading}\n" if heading else ""
        unit_text = f"{heading_prefix}{body}"

        if len(unit_text) > CHUNK_MAX_CHARS:
            flush_pending()
            available = max(CHUNK_MIN_CHARS, CHUNK_MAX_CHARS - len(heading_prefix))
            for part in _split_by_size(body, available, CHUNK_OVERLAP_CHARS):
                merged.append((f"{heading_prefix}{part}", heading))
            continue

        candidate = f"{pending_text}\n\n{unit_text}" if pending_text else unit_text
        if pending_text and len(candidate) > CHUNK_MAX_CHARS:
            flush_pending()
            pending_text = unit_text
            pending_heading = heading
        else:
            pending_text = candidate
            if pending_heading is None:
                pending_heading = heading

        if len(pending_text) >= CHUNK_TARGET_CHARS:
            flush_pending()

    flush_pending()

    # 尾片过短且可安全并入前片时合并，避免文档末尾产生孤立的一两句话。
    if len(merged) >= 2 and len(merged[-1][0]) < CHUNK_MIN_CHARS:
        previous_text, previous_heading = merged[-2]
        tail_text, _ = merged[-1]
        combined = f"{previous_text}\n\n{tail_text}"
        if len(combined) <= CHUNK_MAX_CHARS:
            merged[-2] = (combined, previous_heading)
            merged.pop()

    return [
        Chunk(index=index, text=chunk_text, heading=heading)
        for index, (chunk_text, heading) in enumerate(merged)
    ]


def _split_by_size(text: str, chunk_size: int, overlap: int) -> list[str]:
    """长文本优先在句末切分，找不到边界时才按字符滑窗切分。"""
    if len(text) <= chunk_size:
        return [text]

    result = []
    start = 0
    while start < len(text):
        hard_end = min(start + chunk_size, len(text))
        end = hard_end
        if hard_end < len(text):
            window = text[start:hard_end]
            boundaries = [match.end() for match in re.finditer(r"[。！？；.!?;\n]", window)]
            if boundaries and boundaries[-1] >= CHUNK_MIN_CHARS:
                end = start + boundaries[-1]
        result.append(text[start:end])
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return result


def parse_and_chunk(filename: str, content: bytes) -> list[Chunk]:
    """一站式：校验 -> 提取 -> 切片."""
    validate_file(filename, content)
    text = extract_text(filename, content)
    return chunk_text(text, filename)
