"""供外部智能体调用的本地快报 MCP 服务（stdio）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

import httpx

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from .config import Settings, load_dotenv
from .report.client_trial import fill_client_trial_report, is_client_trial_template, query_client_trial
from .report.pdf import PdfConversionError, convert_docx_to_pdf
from .report.question_library import load_question_library, match_parsed_questions
from .report.supported_templates import generate_supported_report, template_kind
from .report.template import MAX_UPLOAD, decompose_questions, parse_docx
from .tools.pg import PgGateway


@dataclass(frozen=True)
class ReportContext:
    settings: Settings
    gateway: PgGateway
    app: Any = None


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[ReportContext]:
    """每个服务进程共用受控数据库连接池，退出时关闭。"""
    load_dotenv()
    settings = Settings()
    from .server.app import create_app
    app = create_app(settings)
    gateway = app.state.pg
    try:
        yield ReportContext(settings, gateway, app)
    finally:
        await gateway.close()
        await app.state.knowledge.close()


mcp = MCPServer("GeoAgent 快报", lifespan=lifespan)


async def _workflow_api(context: ReportContext, path: str, payload: dict[str, Any] | None = None,
                        content: bytes | None = None) -> dict[str, Any]:
    """进程内调用现有接口，不复制查询、绑定与回填实现。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=context.app),
                                base_url="http://geoagent.local", timeout=120) as client:
        response = await client.post(path, json=payload, content=content)
    if response.is_error:
        detail = response.json().get("detail", "工作流调用失败")
        raise ValueError(str(detail))
    return response.json()


@mcp.tool()
async def start_template_workflow(template_path: str, ctx: Context[ReportContext]) -> dict[str, Any]:
    """解析任意本机DOCX，建立本进程工作流，返回待填位置和候选问题；不自动确认匹配或查询。"""
    source, _ = await asyncio.to_thread(_read_template, template_path)
    content = await asyncio.to_thread(source.read_bytes)
    from urllib.parse import quote
    return await _workflow_api(ctx.request_context.lifespan_context,
                               f"/api/report-templates/parse?filename={quote(source.name)}", content=content)


@mcp.tool()
async def query_template_standard(workflow_id: str, question_id: str, library_id: str,
                                  ctx: Context[ReportContext]) -> dict[str, Any]:
    """执行调用方明确选择的标准问题SQL，复用白名单和统计绑定；候选相似度不等于口径正确。"""
    return await _workflow_api(ctx.request_context.lifespan_context,
                               f"/api/report-templates/{workflow_id}/questions/{question_id}/execute",
                               {"library_id": library_id})


@mcp.tool()
async def query_template_free(workflow_id: str, question_id: str,
                              ctx: Context[ReportContext]) -> dict[str, Any]:
    """未命中标准问题时，实际调用已配置模型和受控查询；将问题及汇总结果发送模型服务，返回结果仅供参考，模糊绑定留待核实。"""
    context = ctx.request_context.lifespan_context
    workflow = context.app.state.template_workflows.get(workflow_id)
    if workflow is None:
        raise ValueError("工作流不存在或服务已重启，请重新解析模板。")
    question = next((q for q in workflow["questions"] if q["id"] == question_id), None)
    if question is None:
        raise ValueError("模板问题不存在。")
    conversation = await _workflow_api(context, "/api/conversations", {"title": "MCP模板自由问数"})
    answer = await _workflow_api(context, f"/api/conversations/{conversation['id']}/messages",
                                 {"content": question["question"]})
    binding = await _workflow_api(context,
        f"/api/report-templates/{workflow_id}/questions/{question_id}/capture-free",
        {"conversation_id": conversation["id"]})
    return {"answer": answer, "binding": binding, "notice": "未命中标准问题库，仅供参考"}


@mcp.tool()
async def generate_template_report(workflow_id: str, ctx: Context[ReportContext],
                                    include_pdf: bool = False) -> dict[str, Any]:
    """使用本工作流实际保存的答案回填并生成Word；未知位置保留待核实。PDF可选且依赖转换环境。"""
    context = ctx.request_context.lifespan_context
    result = await _workflow_api(context, f"/api/report-templates/{workflow_id}/generate",
                                 {"generic_mode": True, "include_pdf": include_pdf})
    for key in ("word", "pdf"):
        if result.get(key):
            result[key]["path"] = str((context.settings.reports_dir / result[key]["filename"]).resolve())
    return result


def _read_template(template_path: str) -> tuple[Path, dict[str, Any]]:
    source = Path(template_path).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".docx":
        raise ValueError("请选择本机存在的 .docx 模板文件。")
    if source.stat().st_size > MAX_UPLOAD:
        raise ValueError("模板不得超过10MB。")
    return source, parse_docx(source.read_bytes(), source.name)


@mcp.tool()
async def inspect_report_template(template_path: str, ctx: Context[ReportContext]) -> dict[str, Any]:
    """解析本机 DOCX，返回原文、提取问题、回填与图表位置及新版问题库候选；不查询数据库。"""
    _, parsed = await asyncio.to_thread(_read_template, template_path)
    settings = ctx.request_context.lifespan_context.settings
    library_path = settings.skills_dir / "qa-library" / "assets" / "question_library.json"
    library = await asyncio.to_thread(load_question_library, library_path)
    questions = match_parsed_questions(decompose_questions(parsed), library)
    return {
        "filename": parsed["filename"],
        "sha256": parsed["sha256"],
        "slot_count": len(parsed["slots"]),
        "question_count": len(questions),
        "client_trial_eligible": is_client_trial_template(parsed),
        "supported_template_kind": template_kind(parsed),
        "question_library_version": library.get("version"),
        "questions": [
            {
                "id": item["id"],
                "source_text": item["source_text"],
                "question": item["question"],
                "slot_ids": item["slot_ids"],
                "candidates": item["question_library_match"]["candidates"],
            }
            for item in questions
        ],
        "chart_anchors": parsed["chart_anchors"],
        "warnings": parsed["warnings"],
        "notice": "标准问题仅为候选；三份已核验原件可用固定生成工具。其它模板可建立动态工作流，查询后按已验证的绑定回填，无法确定的位置标为待核实。",
    }


async def generate_client_trial(template_path: str, context: ReportContext) -> dict[str, Any]:
    """复用现有固定查询和回填，不接受近似模板。"""
    source, parsed = await asyncio.to_thread(_read_template, template_path)
    if not is_client_trial_template(parsed):
        raise ValueError("当前仅支持已核验的快报（删除统计数值）模板；其他模板暂不自动生成。")
    if not context.gateway.configured:
        raise ValueError("未配置 GEOAGENT_PG_DSN，无法查询土地变化数据。")
    started_at = datetime.now().astimezone()
    stats = await query_client_trial(context.gateway, context.settings.skills_dir)
    reports_dir = context.settings.reports_dir
    await asyncio.to_thread(reports_dir.mkdir, parents=True, exist_ok=True)
    word_path = reports_dir / f"快报删除数据版_MCP试运行_{uuid4().hex[:8]}.docx"
    summary = await asyncio.to_thread(
        fill_client_trial_report, source, parsed, stats, word_path,
        query_started_at=started_at,
    )
    pdf_path = word_path.with_suffix(".pdf")
    pdf_error = None
    try:
        await asyncio.to_thread(convert_docx_to_pdf, word_path, pdf_path)
    except PdfConversionError as exc:
        pdf_error = str(exc)
    return {
        "word_path": str(word_path.resolve()),
        "pdf_path": None if pdf_error else str(pdf_path.resolve()),
        "pdf_error": pdf_error,
        "query_started_at": started_at.isoformat(),
        "review_only": True,
        "summary": summary,
        "notice": "试运行报告；待核实项目不可作为正式业务结论。",
    }


@mcp.tool()
async def generate_client_briefing(template_path: str, ctx: Context[ReportContext]) -> dict[str, Any]:
    """用已核验的甲方快报 DOCX 执行固定只读查询，回填并生成 Word/PDF 核验版。"""
    return await generate_client_trial(template_path, ctx.request_context.lifespan_context)


@mcp.tool()
async def generate_supported_briefing(template_path: str, ctx: Context[ReportContext]) -> dict[str, Any]:
    """对已核验的甲方快报、标准问题库示例或师兄挖空原件执行固定只读查询，生成Word/PDF核验版。"""
    source, parsed = await asyncio.to_thread(_read_template, template_path)
    if template_kind(parsed) is None:
        raise ValueError("该模板尚未核验固定统计口径与回填位置，不能自动生成。")
    context = ctx.request_context.lifespan_context
    if not context.gateway.configured:
        raise ValueError("未配置 GEOAGENT_PG_DSN，无法查询土地变化数据。")
    return await generate_supported_report(
        source, parsed, context.gateway,
        context.settings.skills_dir, context.settings.reports_dir,
    )


if __name__ == "__main__":
    mcp.run()
