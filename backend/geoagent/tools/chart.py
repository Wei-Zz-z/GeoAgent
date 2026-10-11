"""统计图表生成工具：把查询/统计结果转为前端可渲染的图表 artifact。

图表类型约定（与前端 ChartView.vue 对齐）：
- pie：单系列占比构成（分段名为 labels，数值为 series[0].values）
- bar：类别对比（可多系列分组柱状图）
- line：随时间/排序的变化趋势（可多系列折线）
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from .registry import get_tools, register_tool
from .result import Artifact, ToolResult

CHART_TYPE_LABELS = {"pie": "饼图", "bar": "柱状图", "line": "折线图"}


class ChartSeries(BaseModel):
    """图表中的一个数值系列。"""

    name: str = Field(..., min_length=1, max_length=40, description="系列名称（如“面积（亩）”）")
    values: list[float] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="数值列表，长度必须与 labels 一致",
    )


class MakeChartParams(BaseModel):
    """生成图表的参数。"""

    chart_type: Literal["pie", "bar", "line"] = Field(
        ...,
        description="图表类型：pie=饼图（构成占比）；bar=柱状图（类别对比）；line=折线图（趋势变化）",
    )
    title: str = Field(..., min_length=1, max_length=100, description="图表标题（含单位，如“建设用地图斑类型构成（亩）”）")
    labels: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="分类/横轴标签：饼图是分段名称，柱/折线图是横轴类别",
    )
    series: list[ChartSeries] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="数值系列；饼图只允许 1 个系列",
    )


@register_tool(
    "make_chart",
    (
        "生成统计图表用于会话窗口可视化。适合：构成/占比问题→pie 饼图；"
        "各类别数量或面积对比（如分地类、分区县统计、Top-N 排行）→bar 柱状图；"
        "随时间或按序变化的趋势→line 折线图。"
        "必须先通过 run_sql 或确定性统计工具取得完整数据，"
        "把分类名称与数值填入 labels/series；饼图只允许 1 个系列且数值非负，"
        "柱/折线图支持多个系列对比。"
    ),
    MakeChartParams,
)
def make_chart(
    chart_type: Literal["pie", "bar", "line"],
    title: str,
    labels: list[str],
    series: list[ChartSeries | dict[str, Any]],
) -> ToolResult:
    """校验并返回图表 artifact（纯数据转换，不调用外部服务）。"""
    n = len(labels)
    if any(not str(label).strip() for label in labels):
        raise ValueError("labels 中存在空名称，请用字典表里的真实地类/行政区名称")
    parsed_series = [
        s if isinstance(s, ChartSeries) else ChartSeries.model_validate(s) for s in series
    ]
    for s in parsed_series:
        if len(s.values) != n:
            raise ValueError(
                f"系列“{s.name}”有 {len(s.values)} 个数值，与 {n} 个 labels 不一致"
            )
        if any(not math.isfinite(v) for v in s.values):
            raise ValueError(f"系列“{s.name}”包含非数值（NaN/Infinity）")

    if chart_type == "pie":
        if len(parsed_series) != 1:
            raise ValueError("饼图只支持 1 个数值系列，占比拆到多个系列无法表达")
        values = parsed_series[0].values
        if any(v < 0 for v in values):
            raise ValueError("饼图数值必须非负（负值无法表达占比）")
        if sum(values) <= 0:
            raise ValueError("饼图数值之和必须大于 0")
    elif n < 2 and chart_type in ("bar", "line"):
        # 柱/折线图至少需要两个点才有对比/趋势意义；单个分类时提示改用饼图或文字。
        raise ValueError("柱状图/折线图至少需要 2 个分类标签")

    data: dict[str, Any] = {
        "chart_type": chart_type,
        "title": title,
        "labels": labels,
        "series": [s.model_dump() for s in parsed_series],
    }
    return ToolResult(
        tool_call_id="",
        name="make_chart",
        content=(
            f"图表已生成：{title}（{CHART_TYPE_LABELS[chart_type]}，"
            f"{n} 个分类），并已作为卡片渲染到会话窗口。最终回答只给结论式总结："
            "关键数字带单位出现一次即可，再点出最值得注意的要点，"
            "不要逐项复述图表中的 labels/values。"
        ),
        artifacts=[Artifact(kind="chart", name=title, data=data)],
    )


def get_chart_tools() -> list[Any]:
    return get_tools("make_chart")
