"""快报 PDF 转换与下载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from geoagent.report import pdf


def test_convert_docx_to_pdf_rejects_missing_input(tmp_path: Path):
    with pytest.raises(pdf.PdfConversionError, match="Word 文件不存在"):
        pdf.convert_docx_to_pdf(tmp_path / "missing.docx")


def test_convert_docx_to_pdf_uses_libreoffice(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.docx"
    source.write_bytes(b"docx")
    expected = tmp_path / "result.pdf"

    monkeypatch.setattr(pdf.shutil, "which", lambda _: "soffice")

    def fake_convert(docx_path: Path, pdf_path: Path, executable: str):
        assert docx_path == source.resolve()
        assert executable == "soffice"
        pdf_path.write_bytes(b"%PDF-1.7")

    monkeypatch.setattr(pdf, "_convert_with_libreoffice", fake_convert)
    assert pdf.convert_docx_to_pdf(source, expected) == expected.resolve()
    assert expected.read_bytes().startswith(b"%PDF")
