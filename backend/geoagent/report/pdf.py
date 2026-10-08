"""将生成的 Word 快报转换为 PDF。"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import re


class PdfConversionError(RuntimeError):
    """当前机器缺少可用的 Word/PDF 转换环境。"""


def _friendly_error(error: Exception) -> str:
    message = str(error)
    if "80070520" in message:
        return "当前后端进程无法访问桌面 Word 登录会话，请在已登录的 Windows 桌面终端中启动后端后重试"
    message = re.sub(r"<[^>]+>", " ", message)
    message = re.sub(r"\s+", " ", message).strip()
    return message[:300]


def _run(command: list[str], timeout: int = 120) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()
        raise PdfConversionError(detail)


def _convert_with_libreoffice(docx_path: Path, pdf_path: Path, executable: str) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="geoagent_lo_") as profile:
        profile_uri = Path(profile).resolve().as_uri()
        _run([
            executable,
            "--headless",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_path.parent),
            str(docx_path),
        ])
    generated = pdf_path.parent / f"{docx_path.stem}.pdf"
    if not generated.is_file():
        raise PdfConversionError("LibreOffice 未生成 PDF 文件")
    if generated.resolve() != pdf_path.resolve():
        generated.replace(pdf_path)


def _convert_with_word(docx_path: Path, pdf_path: Path) -> None:
    """通过 Windows 自带的 PowerShell 调用本机 Microsoft Word。"""
    script = f"""
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
try {{
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $document = $word.Documents.Open('{str(docx_path).replace("'", "''")}')
  $document.ExportAsFixedFormat('{str(pdf_path).replace("'", "''")}', 17)
}} finally {{
  if ($null -ne $document) {{ $document.Close($false) }}
  if ($null -ne $word) {{ $word.Quit() }}
}}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path | None = None) -> Path:
    """把 DOCX 转为 PDF；优先 LibreOffice，Windows 下回退到 Microsoft Word。"""
    docx_path = Path(docx_path).resolve()
    pdf_path = Path(pdf_path or docx_path.with_suffix(".pdf")).resolve()
    if not docx_path.is_file():
        raise PdfConversionError(f"Word 文件不存在: {docx_path}")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    configured = os.getenv("GEOAGENT_PDF_CONVERTER", "").strip()
    executable = configured or shutil.which("soffice") or shutil.which("libreoffice")
    errors: list[str] = []
    if executable:
        try:
            _convert_with_libreoffice(docx_path, pdf_path, executable)
        except (OSError, subprocess.SubprocessError, PdfConversionError) as exc:
            errors.append(f"LibreOffice: {_friendly_error(exc)}")
        else:
            return pdf_path

    if os.name == "nt":
        try:
            _convert_with_word(docx_path, pdf_path)
        except (OSError, subprocess.SubprocessError, PdfConversionError) as exc:
            errors.append(f"Microsoft Word: {_friendly_error(exc)}")
        else:
            if pdf_path.is_file() and pdf_path.stat().st_size > 0:
                return pdf_path
            errors.append("Microsoft Word: 未生成 PDF 文件")

    detail = "；".join(errors) if errors else "未找到 LibreOffice 或 Microsoft Word"
    raise PdfConversionError(f"PDF 转换不可用：{detail}")
