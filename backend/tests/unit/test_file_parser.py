"""
backend/tests/unit/test_file_parser.py

职责: 测试文件校验、文本提取、切片规则（知识库预处理管线）

测试内容:
  - TestValidateFile: 扩展名校验（md/pdf/docx 通过，exe/无扩展名拒绝）/ 大小上限 / 空文件拒绝
  - TestExtractText: Markdown 原样提取（docx/pdf skip 到集成阶段）
  - TestChunkText: Markdown 按 ## 标题切片 / 通用按双换行分段 / 长段落二级切分 / index 连续
  - TestParseAndChunk: 端到端校验+提取+切片
"""

import pytest

from app.utils.file_parser import (
    FileValidationError,
    FileParseError,
    Chunk,
    validate_file,
    extract_text,
    chunk_text,
    parse_and_chunk,
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
)


class TestValidateFile:
    """文件校验测试."""

    def test_valid_markdown(self):
        """有效的 .md 文件应通过校验."""
        content = b"# Hello\n\nSome content here."
        validate_file("test.md", content)  # 不抛异常

    def test_valid_pdf(self):
        """有效的 .pdf 扩展名应通过校验."""
        content = b"%PDF-1.4 fake pdf content"
        validate_file("test.pdf", content)

    def test_valid_docx(self):
        """有效的 .docx 扩展名应通过校验."""
        content = b"PK\x03\x04 fake docx content"
        validate_file("test.docx", content)

    def test_unsupported_format(self):
        """不支持的文件扩展名应被拒绝."""
        with pytest.raises(FileValidationError, match="不支持的文件格式"):
            validate_file("test.exe", b"malicious")

    def test_no_extension(self):
        """无扩展名的文件应被拒绝."""
        with pytest.raises(FileValidationError, match="不支持的文件格式"):
            validate_file("noext", b"content")

    def test_file_too_large(self):
        """超过 10MB 的文件应被拒绝."""
        big_content = b"x" * (MAX_FILE_SIZE + 1)
        with pytest.raises(FileValidationError, match="文件大小超过限制"):
            validate_file("test.md", big_content)

    def test_empty_file(self):
        """空文件应被拒绝."""
        with pytest.raises(FileValidationError, match="文件为空"):
            validate_file("test.md", b"")

    def test_file_at_size_limit(self):
        """恰好等于 10MB 的文件应通过."""
        content = b"x" * MAX_FILE_SIZE
        validate_file("test.md", content)  # 不抛异常


class TestExtractText:
    """文本提取测试."""

    def test_extract_markdown(self):
        """Markdown 文本应原样提取."""
        text = "# Title\n\nHello world."
        content = text.encode("utf-8")
        result = extract_text("test.md", content)
        assert result == text

    def test_extract_docx(self, monkeypatch):
        """Word 文档应提取文本（T34: 桩 python-docx，原 skip 补真测试）。"""
        class _FakeDoc:
            def __init__(self):
                self.paragraphs = [type("P", (), {"text": "Word 段落一"}), type("P", (), {"text": "段落二"})]

        monkeypatch.setattr("docx.Document", lambda *a, **k: _FakeDoc())
        result = extract_text("test.docx", b"fake-docx-bytes")
        assert "Word 段落一" in result and "段落二" in result

    def test_extract_pdf(self, monkeypatch):
        """PDF 文档应提取文本（T34: 桩 PyPDF2，原 skip 补真测试，复用 test_ocr 桩技术）。"""
        class _FakePage:
            def extract_text(self):
                return "PDF 第一页内容"

        class _FakeReader:
            def __init__(self, *a, **k):
                self.pages = [_FakePage()]

        monkeypatch.setattr("pypdf.PdfReader", _FakeReader)
        result = extract_text("test.pdf", b"%PDF-fake")
        assert "PDF 第一页内容" in result


class TestChunkText:
    """文本切片测试."""

    def test_chunk_markdown_with_headings(self):
        """Markdown 文档按 ## 标题切片."""
        text = "## 第一章\n这是第一章的内容。\n\n## 第二章\n这是第二章的内容。"
        result = chunk_text(text, "test.md")
        assert len(result) == 2
        assert result[0].heading == "第一章"
        assert "第一章的内容" in result[0].text
        assert result[1].heading == "第二章"
        assert "第二章的内容" in result[1].text

    def test_chunk_generic_paragraphs(self):
        """通用文档按双换行分段."""
        text = "段落一的内容。\n\n段落二的内容。\n\n段落三的内容。"
        result = chunk_text(text, "test.txt")
        assert len(result) == 3
        assert result[0].index == 0
        assert result[2].index == 2

    def test_chunk_long_paragraph(self):
        """长段落应被二级切分."""
        long_text = "长" * 800  # ~800 chars, > CHUNK_SIZE_CHARS (512)
        result = chunk_text(long_text, "test.txt")
        assert len(result) >= 2  # 至少切成 2 段

    def test_chunk_empty_text(self):
        """空文本返回空列表."""
        result = chunk_text("", "test.md")
        assert result == []

    def test_chunk_single_paragraph(self):
        """单段落文档应返回一个切片."""
        result = chunk_text("唯一的段落。", "test.txt")
        assert len(result) == 1

    def test_chunk_index_continuous(self):
        """切片 index 应连续递增."""
        text = "\n\n".join([f"段落 {i}" for i in range(5)])
        result = chunk_text(text, "test.txt")
        for i, chunk in enumerate(result):
            assert chunk.index == i


class TestParseAndChunk:
    """端到端解析+切片测试."""

    def test_parse_valid_markdown(self):
        """完整流程：校验 + 提取 + 切片."""
        content = "## 标题\n这是测试内容。".encode("utf-8")
        result = parse_and_chunk("test.md", content)
        assert len(result) >= 1
        assert isinstance(result[0], Chunk)

    def test_parse_invalid_format_raises(self):
        """无效格式在校验阶段即被拒绝."""
        with pytest.raises(FileValidationError):
            parse_and_chunk("test.exe", b"data")



def _make_min_xlsx(rows):
    """Construct minimal parseable xlsx with stdlib zipfile."""
    import io as _io
    import zipfile as _zipfile
    shared = []
    for row in rows:
        for cell in row:
            if cell not in shared:
                shared.append(cell)
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="'
        + str(len(shared)) + '">'
        + "".join("<si><t>" + c + "</t></si>" for c in shared)
        + "</sst>"
    )
    sheet_rows = []
    for row in rows:
        cells = "".join(
            '<c r="X' + str(i) + '" t="s"><v>' + str(shared.index(c)) + "</v></c>"
            for i, c in enumerate(row)
        )
        sheet_rows.append("<row>" + cells + "</row>")
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>" + "".join(sheet_rows) + "</sheetData></worksheet>"
    )
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="S" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", shared_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


class TestXlsxSupport:
    """PRD 3.2 spreadsheet support (review P2-1)."""

    def test_xlsx_in_allowed_extensions(self):
        assert ".xlsx" in ALLOWED_EXTENSIONS

    def test_validate_xlsx(self):
        validate_file("table.xlsx", _make_min_xlsx([["order_no", "status"]]))

    def test_extract_xlsx_text(self):
        content = _make_min_xlsx([["order_no", "status"], ["ORD-1", "shipped"]])
        text = extract_text("table.xlsx", content)
        assert "ORD-1" in text
        assert "shipped" in text

    def test_extract_invalid_xlsx_returns_empty(self):
        assert extract_text("bad.xlsx", b"not a zip") == ""
