"""老式 .doc（OLE2）→ .docx 转换：模板解析与回填只支持 OOXML。

模板解析、占位符识别、回填全部基于 ``python-docx``，而它只能读 OOXML（.docx）。
用户手里的快报模板经常是 .doc（OLE2 复合文档），所以上传时先做一次格式转换：

1. 优先 LibreOffice（跨平台、适合服务器；可用 ``GEOAGENT_DOC_CONVERTER`` 指定路径）；
2. Windows 下回退本机 Microsoft Word（PowerShell COM，复用 pdf.py 的做法）。

已经是 .docx 的输入原样返回，不做多余转换。
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# OLE2 复合文档（老式 .doc/.xls/.ppt）与 OOXML（zip）的文件头
OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
ZIP_MAGIC = b"PK\x03\x04"


class DocConversionError(RuntimeError):
    """老式 .doc 无法转换为 .docx。"""


def is_legacy_doc(data: bytes) -> bool:
    """是否 OLE2（老式 .doc）文件。"""
    return bytes(data[:8]) == OLE2_MAGIC


def looks_like_docx(data: bytes) -> bool:
    """是否 OOXML（.docx，本质是 zip）文件。"""
    return bytes(data[:4]) == ZIP_MAGIC


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未知错误").strip()
        raise DocConversionError(detail)


def _friendly_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:300] if text else exc.__class__.__name__


def _convert_with_libreoffice(src: Path, out_dir: Path, executable: str) -> Path:
    """用 LibreOffice 把 .doc 转成 .docx（独立用户配置目录，避免并发冲突）。"""
    with tempfile.TemporaryDirectory(prefix="geoagent_lo_doc_") as profile:
        profile_uri = Path(profile).resolve().as_uri()
        _run([
            executable,
            "--headless",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "docx:MS Word 2007 XML",
            "--outdir",
            str(out_dir),
            str(src),
        ])
    generated = out_dir / f"{src.stem}.docx"
    if not generated.is_file() or generated.stat().st_size == 0:
        raise DocConversionError("LibreOffice 未生成 docx 文件")
    return generated


def _convert_with_word(src: Path, dst: Path) -> Path:
    """通过 PowerShell COM 调用本机 Microsoft Word（Windows 回退方案）。"""
    script = f"""
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
try {{
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $document = $word.Documents.Open('{str(src).replace("'", "''")}', $false, $true)
  $document.SaveAs2('{str(dst).replace("'", "''")}', 16)
}} finally {{
  if ($null -ne $document) {{ $document.Close($false) }}
  if ($null -ne $word) {{ $word.Quit() }}
}}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
    if not dst.is_file() or dst.stat().st_size == 0:
        raise DocConversionError("Microsoft Word 未生成 docx 文件")
    return dst


def _convert_doc_to_docx_file(src: Path, out_dir: Path) -> Path:
    """把 .doc 转成 .docx，返回生成文件路径；两种转换器都不可用时抛错。"""
    configured = os.getenv("GEOAGENT_DOC_CONVERTER", "").strip()
    executable = configured or shutil.which("soffice") or shutil.which("libreoffice")
    errors: list[str] = []
    if executable:
        try:
            return _convert_with_libreoffice(src, out_dir, executable)
        except (OSError, subprocess.SubprocessError, DocConversionError) as exc:
            errors.append(f"LibreOffice: {_friendly_error(exc)}")

    if os.name == "nt":
        try:
            return _convert_with_word(src, out_dir / f"{src.stem}.docx")
        except (OSError, subprocess.SubprocessError, DocConversionError) as exc:
            errors.append(f"Microsoft Word: {_friendly_error(exc)}")

    detail = "；".join(errors) if errors else "未找到 LibreOffice 或 Microsoft Word"
    raise DocConversionError(
        "老式 .doc 模板转换失败（可先用 Word 另存为 .docx 再上传）：" + detail
    )


def ensure_docx(data: bytes, filename: str) -> tuple[bytes, str]:
    """把上传的模板统一成 (.docx 字节, .docx 文件名)。

    - 已经是 .docx：原样返回（只规范扩展名）；
    - 老式 .doc：调用转换器转成 .docx；
    - 其它格式：直接拒绝，避免把不可解析的内容存进工作流。
    """
    name = Path(str(filename or "template")).name or "template"
    stem = Path(name).stem or "template"
    if looks_like_docx(data):
        return data, f"{stem}.docx"
    if not (is_legacy_doc(data) or name.lower().endswith(".doc")):
        raise DocConversionError("模板只支持 .docx，或可转换的老式 .doc 文件。")

    with tempfile.TemporaryDirectory(prefix="geoagent_doc_") as tmp:
        workdir = Path(tmp)
        src = workdir / "source.doc"
        src.write_bytes(data)
        converted = _convert_doc_to_docx_file(src, workdir)
        payload = converted.read_bytes()
        if not looks_like_docx(payload):
            raise DocConversionError("转换结果不是有效的 docx 文件")
        return payload, f"{stem}.docx"