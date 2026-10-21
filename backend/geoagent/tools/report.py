"""土地变化监测快报工具（默认模板；按会话选择的期次统计，含预警分析）。"""

from __future__ import annotations

import os
import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel

from ..datasets import context_table
from .registry import get_tools, register_tool
from .result import Artifact, ToolResult


class GenerateBriefingParams(BaseModel):
    """生成快报参数（默认模板；统计口径跟随当前会话选择的期次，暂无参数）。"""


@register_tool(
    "generate_briefing",
    (
        "按默认模板生成土地变化监测快报（Word .docx）：统计当前会话选择的期次表并回填，"
        "输出到仓库外目录并提供下载。正文包含耕地变化、新增建设用地与疑似违法占地（预警）"
        "三部分；预警依据图斑的永农/红线标记与变化后类型判定，属疑似口径。"
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
    # 数据表跟随会话上方“数据表”选择框：ctx.dataset 由本轮期次解析写入
    table = context_table(ctx)
    period = getattr(ctx, "dataset", None)
    period_label = str(getattr(period, "label", "") or "")
    try:
        stats = await compute_briefing_stats(
            gateway,
            skills_dir=skills_dir,
            table=table,
            period_label=period_label,
        )
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
        # 留空视为未配置：Path("") 会变成当前目录，快报会落进仓库。
        reports_dir = Path(
            os.getenv("GEOAGENT_REPORTS_DIR", "").strip() or str(base_home / "GeoAgent" / "reports")
        )
    label_part = period_label or "全库"
    out_path = reports_dir / f"地类变化监测快报_{label_part}_{uuid4().hex[:8]}.docx"
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

    yongnong = stats.get("yongnong", {}) or {}
    redline = stats.get("redline", {}) or {}
    period_line = (
        f"- 统计期次: {period_label}（{table}）" if period_label else f"- 统计数据表: {table}"
    )
    # 文件走卡片里的预览/下载按钮，正文不暴露本机路径与内部下载链接
    if pdf_error is None:
        formats_line = "- 输出文件: Word + PDF（见上方卡片，可预览 Word / 下载）"
    else:
        formats_line = f"- 输出文件: Word（PDF 未生成：{pdf_error}）"
    lines = [
        "土地变化监测快报已生成：",
        period_line,
        formats_line,
        f"- 变化图斑总数: {stats['total_n']} 个",
        f"- 总面积: {_fmt(stats['total_area'])} 亩",
        f"- 耕地净变化面积: {_fmt(stats['crop_net_area'])} 亩",
        f"- 建设用地(变化后)面积: {_fmt(stats['cur_const']['area'])} 亩",
        f"- 其中原耕地面积: {_fmt(stats['crop2const']['area'])} 亩",
        f"- 疑似占用永久基本农田: {yongnong.get('n', 0)} 个 / "
        f"{_fmt(yongnong.get('area', 0))} 亩",
        f"- 生态保护红线内疑似新增建设: {redline.get('n', 0)} 个 / "
        f"{_fmt(redline.get('area', 0))} 亩",
        "提示：预警口径为“疑似”（涉及永农/红线 + 变化后为建设类），"
        "违法认定需叠加农转用、供地等审批数据。",
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
    artifacts.append(
        Artifact(
            kind="table",
            name="briefing_summary",
            data={
                "columns": ["指标", "数值"],
                "rows": [
                    ["统计期次", period_label or "（默认期次）"],
                    ["统计数据表", table],
                    ["变化图斑总数(个)", stats["total_n"]],
                    ["总面积(亩)", _fmt(stats["total_area"])],
                    ["耕地净变化面积(亩)", _fmt(stats["crop_net_area"])],
                    ["建设用地(变化后)面积(亩)", _fmt(stats["cur_const"]["area"])],
                    ["其中原耕地面积(亩)", _fmt(stats["crop2const"]["area"])],
                    ["疑似占用永久基本农田(个)", yongnong.get("n", 0)],
                    ["疑似占用永久基本农田(亩)", _fmt(yongnong.get("area", 0))],
                    ["红线内疑似新增建设(个)", redline.get("n", 0)],
                    ["红线内疑似新增建设(亩)", _fmt(redline.get("area", 0))],
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
