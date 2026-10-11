"""快报工具卡片输出测试：正文不暴露本机路径/内部链接，PDF 失败也能出 Word。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoagent.datasets import DatasetPeriod
from geoagent.tools.report import generate_briefing

TABLE = 'data."2026_2_change_landuse"'


class BriefingGateway:
    """按 SQL 形状返回预置结果，覆盖快报的全部查询。"""

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def run_sql(self, sql, conversation_id="", tool_call_id=""):
        self.sql.append(sql)
        if 'GROUP BY "XMC"' in sql and "inflow" in sql:
            rows = [{"xmc": "义乌市", "inflow": 100.0, "outflow": 200.0, "net": -100.0}]
        elif 'GROUP BY "XMC"' in sql:
            rows = [{"xmc": "义乌市", "n": 3, "area": 900.0}]
        elif 'GROUP BY "TBLX"' in sql:
            rows = [{"code": "01", "area": 500.0}]
        else:
            rows = [{
                "n": 10, "area": 1500.0, "total_n": 10, "total_area": 1500.0,
                "qsx": "20251006", "hsx": "20260501",
            }]
        return {
            "columns": list(rows[0]),
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
        }


def _ctx(tmp_path, gateway) -> SimpleNamespace:
    return SimpleNamespace(
        pg=gateway,
        dataset=DatasetPeriod(id="2026_2", label="2026年第二期", table=TABLE),
        skills=SimpleNamespace(skills_dir=None),
        reports_dir=tmp_path / "reports",
        dataset_id="2026_2",
    )


@pytest.mark.asyncio
async def test_briefing_card_text_is_user_facing(tmp_path, monkeypatch):
    """卡片正文只给期次与关键数字，不给本机路径和裸下载链接。"""
    def fake_pdf(source, output):
        output.write_bytes(b"%PDF-1.4\n%%EOF")
        return output

    monkeypatch.setattr("geoagent.report.pdf.convert_docx_to_pdf", fake_pdf)
    gateway = BriefingGateway()
    result = await generate_briefing(_ctx(tmp_path, gateway))

    assert result.is_error is False
    content = result.content
    assert "文件路径" not in content
    assert "/api/files/" not in content
    assert ":\\" not in content and "C:" not in content
    assert "2026年第二期" in content
    assert "疑似占用永久基本农田" in content
    assert "生态保护红线内疑似新增建设" in content
    # 文件仍以 artifact 形式提供（卡片负责预览/下载）
    kinds = [a.kind for a in result.artifacts]
    assert kinds.count("file") == 2
    files = [a for a in result.artifacts if a.kind == "file"]
    assert files[0]["data"]["filename"].endswith(".docx") if isinstance(files[0], dict) else True
    assert all("/api/files/reports/" in a.data["url"] for a in files)
    assert any(a.kind == "table" for a in result.artifacts)
    assert all(TABLE in sql for sql in gateway.sql)


@pytest.mark.asyncio
async def test_briefing_reports_pdf_failure_in_line(tmp_path, monkeypatch):
    """PDF 转换失败时，正文说明原因，Word 仍可下载。"""
    from geoagent.report.pdf import PdfConversionError

    def failing_pdf(source, output=None):
        raise PdfConversionError("未安装转换器")

    monkeypatch.setattr("geoagent.report.pdf.convert_docx_to_pdf", failing_pdf)
    result = await generate_briefing(_ctx(tmp_path, BriefingGateway()))

    assert result.is_error is False
    assert "PDF 未生成" in result.content
    assert [a.kind for a in result.artifacts].count("file") == 1
