from pathlib import Path

import pytest
from mcp import Client
from docx import Document

from geoagent import mcp_server
from geoagent.report.question_library import load_question_library
from geoagent.config import DEFAULT_PG_WHITELIST
from geoagent.tools.pg import validate_select_sql


@pytest.mark.asyncio
async def test_mcp_client_discovers_tools() -> None:
    async with Client(mcp_server.mcp) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {
            "inspect_report_template", "generate_client_briefing", "generate_supported_briefing",
            "start_template_workflow", "query_template_standard", "query_template_free", "generate_template_report",
        }
        generated = next(tool for tool in tools.tools if tool.name == "generate_client_briefing")
        assert "template_path" in generated.input_schema["properties"]


@pytest.mark.asyncio
async def test_mcp_client_can_inspect_template(tmp_path: Path) -> None:
    source = tmp_path / "sample.docx"
    document = Document()
    document.add_paragraph("本期变化图斑***个。")
    document.save(source)
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("inspect_report_template", {"template_path": str(source)})
    assert result.is_error is False
    assert result.structured_content["client_trial_eligible"] is False
    assert result.structured_content["questions"][0]["slot_ids"] == ["b0:s0"]


def test_updated_question_library_keeps_existing_entries() -> None:
    asset = Path(__file__).resolve().parents[2] / "skills/qa-library/assets/question_library.json"
    library = load_question_library(asset)
    ids = [entry["id"] for entry in library["entries"]]
    assert library["version"] == "1.1"
    assert len(ids) == 37
    assert len(set(ids)) == 37
    assert {"total_summary", "by_type_summary", "farmland_inflow_source"}.issubset(ids)
    for entry in library["entries"]:
        if entry.get("sql"):
            validate_select_sql(entry["sql"], DEFAULT_PG_WHITELIST)


@pytest.mark.asyncio
async def test_mcp_rejects_unknown_template_before_query(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "other.docx"
    source.write_bytes(b"test")
    monkeypatch.setattr(mcp_server, "_read_template", lambda _: (source, {"sha256": "other"}))

    class Gateway:
        configured = True

        async def run_sql(self, *_args, **_kwargs):
            raise AssertionError("未知模板不可查询数据库")

    context = mcp_server.ReportContext(settings=object(), gateway=Gateway())
    with pytest.raises(ValueError, match="仅支持已核验"):
        await mcp_server.generate_client_trial(str(source), context)


@pytest.mark.asyncio
async def test_mcp_returns_word_and_pdf_failure_separately(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "client.docx"
    source.write_bytes(b"test")
    monkeypatch.setattr(mcp_server, "_read_template", lambda _: (source, {"sha256": "ok"}))
    monkeypatch.setattr(mcp_server, "is_client_trial_template", lambda _: True)

    async def fake_query(_gateway, _skills_dir):
        return {"total": {}, "cities": [], "counties": []}

    def fake_fill(_source, _parsed, _stats, output, *, query_started_at):
        output.write_bytes(b"word")
        return {"filled_scalars": 15, "pending_scalars": ["违法占地"]}

    def fake_pdf(_source, _output):
        raise mcp_server.PdfConversionError("PDF环境不可用")

    monkeypatch.setattr(mcp_server, "query_client_trial", fake_query)
    monkeypatch.setattr(mcp_server, "fill_client_trial_report", fake_fill)
    monkeypatch.setattr(mcp_server, "convert_docx_to_pdf", fake_pdf)

    class Settings:
        reports_dir = tmp_path / "reports"
        skills_dir = tmp_path / "skills"

    class Gateway:
        configured = True

    result = await mcp_server.generate_client_trial(
        str(source), mcp_server.ReportContext(Settings(), Gateway())
    )
    assert Path(result["word_path"]).is_file()
    assert result["pdf_path"] is None
    assert "PDF环境不可用" in result["pdf_error"]
    assert result["review_only"] is True
    assert "违法占地" in result["summary"]["pending_scalars"]
