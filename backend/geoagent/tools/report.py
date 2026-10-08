"""土地变化监测快报工具（v1，默认模板，全库统计）。"""

from __future__ import annotations

import os
import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel

from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


class GenerateBriefingParams(BaseModel):
    """生成快报参数（v1 全库、默认模板，暂无参数）。"""


@register_tool(
    "generate_briefing",
    (
        "按默认模板生成土地变化监测快报（Word .docx）：对全库执行预定义统计并回填，"
        "输出到仓库外目录并提供下载。疑似违法占地部分暂缺数据、留空。"
    ),
    GenerateBriefingParams,
)
async def generate_briefing(ctx: Any) -> ToolResult:
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return ToolResult(
            tool_call_id="",
            name="generate_briefing",
            content="数据库未接入：请先配置 GEOAGENT_PG_DSN。",
            is_error=True,
        )
    # 函数内懒加载，避免 tools 包与 report 包之间的循环导入。
    from ..report.briefing import build_briefing_docx, compute_briefing_stats, _fmt
    from ..report.pdf import PdfConversionError, convert_docx_to_pdf

    skills = getattr(ctx, "skills", None)
    skills_dir = getattr(skills, "skills_dir", None) if skills is not None else None
    try:
        stats = await compute_briefing_stats(gateway, skills_dir=skills_dir)
    except Exception as exc:
        return ToolResult(
            tool_call_id="",
            name="generate_briefing",
            content=f"快报统计失败: {type(exc).__name__}: {exc}",
            is_error=True,
        )
    reports_dir = getattr(ctx, "reports_dir", None)
    if reports_dir is None:
        base_home = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        reports_dir = Path(os.getenv("GEOAGENT_REPORTS_DIR", str(base_home / "GeoAgent" / "reports")))
    out_path = reports_dir / f"地类变化监测快报_2026年第一期_{uuid4().hex[:8]}.docx"
    try:
        await asyncio.to_thread(build_briefing_docx, stats, out_path)
    except Exception as exc:
        return ToolResult(
            tool_call_id="",
            name="generate_briefing",
            content=f"Word 生成失败: {type(exc).__name__}: {exc}",
            is_error=True,
        )

    pdf_path = out_path.with_suffix(".pdf")
    pdf_error = None
    try:
        await asyncio.to_thread(convert_docx_to_pdf, out_path, pdf_path)
    except PdfConversionError as exc:
        pdf_error = str(exc)

    lines = [
        "土地变化监测快报已生成（Word）：",
        f"文件路径: {out_path}",
        f"下载: /api/files/reports/{quote(out_path.name)}",
        f"- 变化图斑总数: {stats['total_n']} 个",
        f"- 总面积: {_fmt(stats['total_area'])} 亩",
        f"- 耕地净变化面积: {_fmt(stats['crop_net_area'])} 亩",
        f"- 建设用地(变化后)面积: {_fmt(stats['cur_const']['area'])} 亩",
        f"- 其中原耕地面积: {_fmt(stats['crop2const']['area'])} 亩",
    ]
    artifacts = [
        Artifact(
            kind="file",
            name="briefing_docx",
            data={
                "url": f"/api/files/reports/{quote(out_path.name)}",
                "filename": out_path.name,
            },
        )
    ]
    if pdf_error is None:
        lines.insert(3, f"PDF下载: /api/files/reports/{quote(pdf_path.name)}")
        artifacts.append(
            Artifact(
                kind="file",
                name="briefing_pdf",
                data={
                    "url": f"/api/files/reports/{quote(pdf_path.name)}",
                    "filename": pdf_path.name,
                },
            )
        )
    else:
        lines.insert(3, f"PDF转换未完成（Word仍可正常下载）：{pdf_error}")
    artifacts.append(
        Artifact(
            kind="table",
            name="briefing_summary",
            data={
                "columns": ["指标", "数值"],
                "rows": [
                    ["变化图斑总数(个)", stats["total_n"]],
                    ["总面积(亩)", _fmt(stats["total_area"])],
                    ["耕地净变化面积(亩)", _fmt(stats["crop_net_area"])],
                    ["建设用地(变化后)面积(亩)", _fmt(stats["cur_const"]["area"])],
                    ["其中原耕地面积(亩)", _fmt(stats["crop2const"]["area"])],
                ],
            },
        )
    )
    return ToolResult(
        tool_call_id="",
        name="generate_briefing",
        content="\n".join(lines),
        artifacts=artifacts,
    )


def get_report_tools() -> list[Any]:
    return get_tools("generate_briefing")
