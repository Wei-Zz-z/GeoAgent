"""预警分析工具：把疑似违规图斑连同边界一起返回，前端用 OpenLayers 叠加底图展示。

口径（与 ``skills/qa-library/assets/question_library.json`` 一致）：

- 疑似占用永久基本农田：``SFYN='是'`` 且变化后图斑类型属于建设用地（含疑似）；
- 疑似占用生态保护红线：``SFHX='是'`` 且变化后图斑类型属于建设用地（含疑似）。

三条纪律：

1. **只给"疑似"**：是否违法需叠加农转用、供地等审批数据，工具文本必须写清楚；
2. **没有几何就说没有**：2026 年第一期 ``geom`` 全为空，此时只给属性与面积并说明
   无法出图，不得拿别的期次几何顶替；
3. **支持行政区下钻**：传 ``county`` 时只统计该县（``"XMC"``），不传才是全库口径；
   地方名来自用户或模型，拼进 SQL 前必须转义。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..datasets import context_table
from .categories import construction_codes
from .labels import TBLX_LABELS
from .registry import get_tools, register_tool
from .result import Artifact, ToolResult
from .units import m2_to_mu

WARNING_SCOPES: dict[str, dict[str, str]] = {
    "yongnong": {
        "column": "SFYN",
        "scope_label": "永久基本农田",
        "title": "疑似占用永久基本农田转为建设用地的图斑",
        "rule": "涉及永久基本农田（SFYN=是）且变化后图斑类型属于建设用地（含疑似）模糊集",
    },
    "redline": {
        "column": "SFHX",
        "scope_label": "生态保护红线",
        "title": "生态保护红线内疑似新增建设图斑",
        "rule": "涉及生态保护红线（SFHX=是）且变化后图斑类型属于建设用地（含疑似）模糊集",
    },
}

# 预警图斑在前端用红色描边 + 半透明红填充标识（随 GeoJSON 一起下发）
WARNING_STYLE: dict[str, Any] = {
    "stroke": "#d93025",
    "strokeWidth": 2,
    "fill": "rgba(217, 48, 37, 0.28)",
    "label": "预警图斑",
}

# 明细表格默认展示面积最大的前 N 个（地图单独按 max_features 控制）
DETAIL_LIMIT = 20
# GeoJSON 用 WGS84 输出，前端按 EPSG:4326 → EPSG:3857 渲染
GEO_SRID = 4326


class WarningPolygonsParams(BaseModel):
    county: str = Field(
        default="",
        description=(
            "限定县级行政区名称（与主表 XMC 一致，如“缙云县”）；留空表示全库。"
            "用户提到某个县/区时必须传，否则会统计到全库"
        ),
    )
    max_features: int = Field(
        default=500,
        ge=1,
        le=1000,
        description="地图上最多返回的图斑数量（按面积从大到小取前 N 个）",
    )


def _clean_text(value: Any) -> str:
    """清洗用户/模型给出的地名：去首尾空白并转义单引号，防注入。"""
    return str(value or "").strip().replace("'", "''")


def _period_label(ctx: Any) -> str:
    period = getattr(ctx, "dataset", None)
    return str(getattr(period, "label", "") or "")


def _where(column: str, county: str = "") -> str:
    # 预警按“建设用地（含疑似）”口径：动土/推堆土/瓦砾/推平也要纳入（共 19 类）
    codes = ", ".join(f"'{code}'" for code in construction_codes(include_suspect=True))
    condition = f'"{column}" = \'是\' AND "TBLX" IN ({codes})'
    name = _clean_text(county)
    if name:
        condition += f' AND "XMC" = \'{name}\''
    return condition


def _total_sql(table: str, where: str) -> str:
    return f'SELECT count(*) AS n, sum("MJ") AS area_m2 FROM {table} WHERE {where}'


def _detail_sql(table: str, where: str) -> str:
    return (
        'SELECT "OBJECTID" AS objectid, "XMC" AS county, "TBLX" AS tblx, "DLMC" AS dlmc, '
        '"MJ" AS area_m2, "SFYN" AS sfyn, "SFHX" AS sfhx '
        f'FROM {table} WHERE {where} ORDER BY "MJ" DESC LIMIT {DETAIL_LIMIT}'
    )


def _geo_sql(table: str, where: str, max_features: int) -> str:
    """一行返回完整 FeatureCollection（含属性），避免被行数上限截断。"""
    return (
        "SELECT count(*) AS shown_n, count(p.geom) AS with_geom, "
        "json_build_object('type', 'FeatureCollection', 'features', "
        "COALESCE(json_agg(json_build_object('type', 'Feature', "
        "'geometry', ST_AsGeoJSON(ST_Transform(p.geom, "
        f"{GEO_SRID}))::json, "
        "'properties', json_build_object('objectid', p.objectid, 'county', p.county, "
        "'tblx', p.tblx, 'dlmc', p.dlmc, 'area_mu', p.area_mu))), '[]'::json)) AS fc "
        'FROM (SELECT "OBJECTID" AS objectid, "XMC" AS county, "TBLX" AS tblx, '
        '"DLMC" AS dlmc, round(("MJ" * 0.0015)::numeric, 2) AS area_mu, geom '
        f'FROM {table} WHERE {where} AND geom IS NOT NULL '
        f'ORDER BY "MJ" DESC LIMIT {int(max_features)}) p'
    )


def _detail_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "主键": row.get("objectid"),
                "行政区": row.get("county"),
                "图斑类型": TBLX_LABELS.get(str(row.get("tblx")), row.get("tblx")),
                "原地类": row.get("dlmc"),
                "面积(亩)": m2_to_mu(row.get("area_m2")),
                "涉及永农": row.get("sfyn"),
                "涉及红线": row.get("sfhx"),
            }
        )
    return out


async def _run_warning(
    ctx: Any,
    scope: str,
    county: str,
    max_features: int,
    tool_name: str,
) -> ToolResult:
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return ToolResult(
            tool_call_id="",
            name=tool_name,
            content="数据库未接入：请先配置 GEOAGENT_PG_DSN。",
            is_error=True,
        )
    spec = WARNING_SCOPES[scope]
    table = context_table(ctx)
    where = _where(spec["column"], county)
    region = _clean_text(county)
    period = _period_label(ctx)
    # 限定了区县时标题优先显示区县，避免与整表口径混淆
    scope_note = f"（{region}）" if region else (f"（{period}）" if period else "")
    scope_text = region or period or "当前期次"

    try:
        total_rows = (
            await gateway.run_sql(_total_sql(table, where), conversation_id=tool_name)
        )["rows"]
        detail_rows = (
            await gateway.run_sql(_detail_sql(table, where), conversation_id=tool_name)
        )["rows"]
        geo_error = ""
        geo_row: dict[str, Any] = {}
        try:
            geo_row = (
                await gateway.run_sql(
                    _geo_sql(table, where, max_features), conversation_id=tool_name
                )
            )["rows"][0]
        except Exception as exc:  # 该期次没有几何列 / 坐标系不可转换
            geo_error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return ToolResult(
            tool_call_id="",
            name=tool_name,
            content=f"预警统计失败: {type(exc).__name__}: {exc}",
            is_error=True,
        )

    total = total_rows[0] if total_rows else {"n": 0, "area_m2": 0}
    total_n = int(total.get("n") or 0)
    total_area = m2_to_mu(total.get("area_m2") or 0)

    if total_n == 0:
        return ToolResult(
            tool_call_id="",
            name=tool_name,
            content=(
                f"{scope_text}没有匹配到{spec['title']}（口径：{spec['rule']}）。"
                "请确认行政区名称与期次选择是否正确。"
            ),
            artifacts=[],
        )

    artifacts: list[Artifact] = [
        Artifact(
            kind="table",
            name=f"{spec['title']}{scope_note}明细（面积前 {DETAIL_LIMIT}）",
            data={
                "columns": ["主键", "行政区", "图斑类型", "原地类", "面积(亩)", "涉及永农", "涉及红线"],
                "rows": _detail_rows(detail_rows),
            },
        )
    ]

    features = 0
    shown_n = 0
    if geo_row:
        payload = geo_row.get("fc")
        try:
            collection = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError):
            collection = None
        if isinstance(collection, dict):
            features = len(collection.get("features") or [])
            shown_n = int(geo_row.get("shown_n") or features)
            if features:
                # 带上红色样式提示：前端按它渲染预警图层
                styled = dict(collection)
                styled["style"] = dict(WARNING_STYLE)
                artifacts.insert(
                    0,
                    Artifact(
                        kind="geojson",
                        name=f"{spec['title']}{scope_note}分布图",
                        data=styled,
                    ),
                )

    lines = [
        f"{spec['title']}{scope_note}：{total_n:,} 个，合计 {total_area:,.2f} 亩"
        f"（口径：{spec['rule']}）。",
    ]
    if features:
        note = (
            f"地图按面积从大到小展示前 {shown_n} 个图斑边界；"
            if shown_n < total_n
            else f"地图已展示全部 {features} 个图斑边界；"
        )
        lines.append(note + f"表格列出面积最大的前 {len(detail_rows)} 个，完整属性见表格卡片。")
    else:
        reasons = [f"{scope_text}的几何字段 geom 为空或不可用"]
        if geo_error:
            reasons.append(f"取几何失败：{geo_error}")
        lines.append(
            "、".join(reasons)
            + "，本轮只能给出数量、面积与属性明细，无法在地图上展示图斑边界。"
        )
    lines.append(
        "注意：本结果只是“疑似”（依据是否涉及永农/红线标记与变化后类型），"
        "是否违法必须叠加农转用、供地等审批数据后判定。"
    )
    return ToolResult(
        tool_call_id="",
        name=tool_name,
        content="\n".join(lines),
        artifacts=artifacts,
    )


@register_tool(
    "warning_yongnong_polygons",
    (
        "预警分析：列出疑似占用永久基本农田转为建设用地的图斑，返回图斑边界（GeoJSON，"
        "前端红色叠加底图）+ 属性明细表 + 数量面积汇总。"
        "用于“违规/疑似占用永久基本农田的图斑有哪些/分布在哪”类问题；"
        "用户指定了某个县（如“缙云县”）时必须传 county，否则结果会是全库口径。"
        "只给疑似结论，是否违法需审批数据。"
    ),
    WarningPolygonsParams,
)
async def warning_yongnong_polygons(
    ctx: Any, county: str, max_features: int
) -> ToolResult:
    return await _run_warning(ctx, "yongnong", county, max_features, "warning_yongnong_polygons")


@register_tool(
    "warning_redline_polygons",
    (
        "预警分析：列出生态保护红线内疑似新增建设的图斑，返回图斑边界（GeoJSON，"
        "前端红色叠加底图）+ 属性明细表 + 数量面积汇总。"
        "用于“红线内有没有新增建设/红线内建设图斑分布”类问题；"
        "用户指定了某个县时必须传 county，否则结果会是全库口径。"
        "只给疑似结论，是否违法需审批数据。"
    ),
    WarningPolygonsParams,
)
async def warning_redline_polygons(
    ctx: Any, county: str, max_features: int
) -> ToolResult:
    return await _run_warning(ctx, "redline", county, max_features, "warning_redline_polygons")


def get_warning_tools() -> list[Any]:
    return get_tools("warning_yongnong_polygons", "warning_redline_polygons")