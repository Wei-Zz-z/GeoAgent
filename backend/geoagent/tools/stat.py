"""高频统计模式的确定性工具（后端预写 SQL，避免模型手拼出错）。

前后时项口径：
- 前时项（原土地类型 DLBM/DLMC）：通过合并字典可落到 二级类 → 一级类 → 三大类
  （CATEGORY_NAME 为空时按 canonical 规则兜底：K/1104A/1107A→农用地，
  建设用地正则→建设用地，其余→其他）；
- 后时项（图斑类型 TBLX）：只有一级，按 TBLX→三大类模糊映射（默认模板）归入
  农用地/建设用地/未利用地，两侧在三大类层面可比。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..datasets import context_table
from .categories import category_codes
from .registry import get_tools, register_tool
from .units import convert_mu_headers, m2_to_mu
from .result import Artifact, ToolResult

# 期次表名不写死：每次调用由 datasets.context_table(ctx) 按当期期次解析。
CROP_CURRENT = ("01", "1")


def _codes_in(codes: list[str]) -> str:
    return ", ".join(f"'{c}'" for c in codes)


def _category_case(codes_agricultural: list[str], codes_construction: list[str]) -> str:
    """按 TBLX 模糊映射生成 三大类 CASE（未收录归为 其他）。"""
    agri = _codes_in(codes_agricultural)
    const = _codes_in(codes_construction)
    return (
        f'CASE WHEN "TBLX" IN ({const}) THEN \'建设用地\' '
        f'WHEN "TBLX" IN ({agri}) THEN \'农用地\' '
        f'WHEN "TBLX" IN ({_codes_in(category_codes("unused"))}) THEN \'未利用地\' '
        "ELSE '其他' END"
    )


ORIG_CATEGORY_CASE = (
    'CASE WHEN d."CATEGORY_NAME" IS NOT NULL THEN d."CATEGORY_NAME" '
    'WHEN t."DLBM" ~ \'^(05|06|07|08|09|100[1-5]|100[7-9]|1109|1201)\' THEN \'建设用地\' '
    'WHEN t."DLBM" LIKE \'%K\' OR t."DLBM" IN (\'1104A\', \'1107A\', \'1208\') '
    "THEN '农用地' ELSE '其他' END"
)

def _orig_summary_join(table: str) -> str:
    """原土地类型侧的口径汇总 JOIN（表名按当期期次注入）。"""
    return (
        f"FROM {table} t "
        "LEFT JOIN knowledge_base.dict_land_classification_summary d "
        'ON t."DLBM" = d."DLBM"'
    )


class SummarizeByTypeParams(BaseModel):
    """按图斑类型（变化后）汇总（无参数）。"""


class FragmentStatsParams(BaseModel):
    threshold_m2: float = Field(
        gt=0,
        description="面积阈值（平方米，按库内 MJ 判定）：统计面积小于该值的图斑。注意数据面积值域为 50-200 平方米；返回结果中的面积已换算为亩",
    )


class FarmlandFlowSummaryParams(BaseModel):
    """耕地流出/流入/净变化 与流向/来源构成（无参数，固定口径）。"""


class ConstructionChangeSummaryParams(BaseModel):
    top_n: int = Field(
        default=10,
        ge=1,
        le=100,
        description="分县表格中显示的行数（按新增面积排序取前 N）",
    )


class TopConversionsParams(BaseModel):
    top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        description="返回的转换类型数量（前 N）",
    )


def _table(columns: list[str], rows: list[dict[str, Any]], name: str) -> Artifact:
    """建表前把面积列统一换算为亩（库内 MJ 与统计列都是平方米）。"""
    mu_columns, mu_rows = convert_mu_headers(columns, rows)
    return Artifact(kind="table", name=name, data={"columns": mu_columns, "rows": mu_rows})


@register_tool(
    "summarize_by_type",
    (
        "按图斑类型（变化后 TBLX）确定性汇总：每种类型的图斑数与总面积，以及全库合计。"
        "用于“每种类型总面积/按图斑类型汇总”类问题。"
    ),
    SummarizeByTypeParams,
)
async def summarize_by_type(ctx: Any) -> ToolResult:
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return _no_gateway("summarize_by_type")
    table = context_table(ctx)
    try:
        detail = await gateway.run_sql(
            "SELECT t.\"TBLX\" AS code, "
            "COALESCE(d.\"TBLX_CODE\", t.\"TBLX\") AS name, "
            "count(*) AS n, sum(t.\"MJ\") AS area_m2 "
            f"FROM {table} t "
            "LEFT JOIN knowledge_base.dict_tblx d "
            "ON t.\"TBLX\" = d.\"TBLX\" OR t.\"TBLX\" = '0' || d.\"TBLX\" "
            "GROUP BY t.\"TBLX\", d.\"TBLX_CODE\" ORDER BY area_m2 DESC",
            conversation_id="summarize_by_type",
        )
        total = await gateway.run_sql(
            f"SELECT count(*) AS total_n, sum(\"MJ\") AS total_area_m2 FROM {table}",
            conversation_id="summarize_by_type",
        )
    except Exception as exc:
        return _stat_error("summarize_by_type", exc)
    rows = detail["rows"]
    total_row = total["rows"][0] if total["rows"] else {}
    lines = _summary_lines(rows, total_row.get("total_n") or 0, float(total_row.get("total_area_m2") or 0))
    return ToolResult(
        tool_call_id="",
        name="summarize_by_type",
        content="\n".join(lines),
        artifacts=[
            _table(
                ["图斑类型", "图斑数(个)", "面积(平方米)"],
                [
                    {
                        "图斑类型": row["name"],
                        "图斑数(个)": row["n"],
                        "面积(平方米)": round(float(row["area_m2"]), 2),
                    }
                    for row in rows
                ],
                "by_type_summary",
            )
        ],
    )


@register_tool(
    "fragment_stats",
    (
        "统计面积（MJ）小于阈值的图斑数量及占全库数量/面积的比例。"
        "用于“面积小于 XX 平方米的细碎图斑”类问题。"
    ),
    FragmentStatsParams,
)
async def fragment_stats(ctx: Any, threshold_m2: float) -> ToolResult:
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return _no_gateway("fragment_stats")
    table = context_table(ctx)
    try:
        total = await gateway.run_sql(
            f"SELECT count(*) AS n, sum(\"MJ\") AS area FROM {table}",
            conversation_id="fragment_stats",
        )
        frag = await gateway.run_sql(
            f'SELECT count(*) AS n, sum("MJ") AS area FROM {table} '
            f'WHERE "MJ" < {float(threshold_m2):.6g}',
            conversation_id="fragment_stats",
        )
    except Exception as exc:
        return _stat_error("fragment_stats", exc)
    t = total["rows"][0] if total["rows"] else {"n": 0, "area": 0}
    f = frag["rows"][0] if frag["rows"] else {"n": 0, "area": 0}
    tn, ta = t["n"] or 0, float(t["area"] or 0)
    fn, fa = f["n"] or 0, float(f["area"] or 0)
    share_n = fn / tn * 100 if tn else 0.0
    share_a = fa / ta * 100 if ta else 0.0
    content = (
        f"面积小于 {threshold_m2:g} 平方米的图斑 {fn:,} 个，占全库 {tn:,} 个的 "
        f"{share_n:.2f}%；其面积 {m2_to_mu(fa):,.2f} 亩，占全库面积 {share_a:.2f}%。\n"
        "注意：阈值按库内平方米（MJ）判定；数据面积值域为 50-200 平方米，阈值 ≤50 时结果恒为 0。"
    )
    return ToolResult(
        tool_call_id="",
        name="fragment_stats",
        content=content,
        artifacts=[
            _table(
                ["指标", "数值"],
                [
                    ["全库图斑数(个)", tn],
                    ["全库面积(平方米)", round(ta, 2)],
                    [f"面积<{threshold_m2:g}㎡ 图斑数(个)", fn],
                    [f"面积<{threshold_m2:g}㎡ 面积(平方米)", round(fa, 2)],
                    [f"数量占比(%)", round(share_n, 2)],
                    [f"面积占比(%)", round(share_a, 2)],
                ],
                "fragment_stats",
            )
        ],
    )


@register_tool(
    "farmland_flow_summary",
    (
        "耕地前后流向确定性汇总：流出（原耕地 DLBM 01%）/流入（现耕地 TBLX 01）/"
        "净变化，以及流出方向（按现三大类模糊映射）与流入来源（按原三大类）。"
        "用于“耕地流出/流入/净变化/流向”类问题。"
    ),
    FarmlandFlowSummaryParams,
)
async def farmland_flow_summary(ctx: Any) -> ToolResult:
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return _no_gateway("farmland_flow_summary")
    table = context_table(ctx)
    try:
        outflow = await gateway.run_sql(
            f'SELECT count(*) AS n, sum("MJ") AS area FROM {table} WHERE "DLBM" LIKE \'01%\'',
            conversation_id="farmland_flow",
        )
        inflow = await gateway.run_sql(
            f'SELECT count(*) AS n, sum("MJ") AS area FROM {table} WHERE "TBLX" IN ({_codes_in(list(CROP_CURRENT))})',
            conversation_id="farmland_flow",
        )
        cat = _category_case(category_codes("agricultural"), category_codes("construction"))
        flows = await gateway.run_sql(
            f'SELECT {cat} AS category, count(*) AS n, sum("MJ") AS area '
            f"FROM {table} WHERE \"DLBM\" LIKE '01%' GROUP BY {cat} ORDER BY area DESC",
            conversation_id="farmland_flow",
        )
        sources = await gateway.run_sql(
            f"SELECT {ORIG_CATEGORY_CASE} AS category, count(*) AS n, sum(t.\"MJ\") AS area "
            f"{_orig_summary_join(table)} WHERE t.\"TBLX\" IN ({_codes_in(list(CROP_CURRENT))}) "
            "GROUP BY category ORDER BY area DESC",
            conversation_id="farmland_flow",
        )
        details = await gateway.run_sql(
            "SELECT COALESCE(d.\"TBLX_CODE\", t.\"TBLX\") AS name, count(*) AS n, "
            "sum(t.\"MJ\") AS area "
            f"FROM {table} t "
            "LEFT JOIN knowledge_base.dict_tblx d "
            "ON t.\"TBLX\" = d.\"TBLX\" OR t.\"TBLX\" = '0' || d.\"TBLX\" "
            'WHERE t."DLBM" LIKE \'01%\' GROUP BY name ORDER BY area DESC LIMIT 10',
            conversation_id="farmland_flow",
        )
    except Exception as exc:
        return _stat_error("farmland_flow_summary", exc)
    o = outflow["rows"][0] if outflow["rows"] else {"n": 0, "area": 0}
    i = inflow["rows"][0] if inflow["rows"] else {"n": 0, "area": 0}
    on, oa = o["n"] or 0, float(o["area"] or 0)
    inn, ia = i["n"] or 0, float(i["area"] or 0)
    metrics = [
        ["耕地流出(原 DLBM 01%)", on, round(oa, 2)],
        ["耕地流入(现 TBLX 01)", inn, round(ia, 2)],
        ["耕地净变化(流入−流出)", inn - on, round(ia - oa, 2)],
    ]
    content = [
        f"耕地流出 {on:,} 个 / {m2_to_mu(oa):,.2f} 亩；流入 {inn:,} 个 / {m2_to_mu(ia):,.2f} 亩；"
        f"净变化 {inn - on:,} 个 / {m2_to_mu(ia - oa):,.2f} 亩。",
        "流出方向（原耕地 → 现三大类，模糊映射）：",
    ]
    content += [f"- {r['category']}: {r['n']:,} 个 / {m2_to_mu(r['area']):,.2f} 亩" for r in flows["rows"]]
    content.append("流入来源（现耕地 ← 原三大类）：")
    content += [f"- {r['category']}: {r['n']:,} 个 / {m2_to_mu(r['area']):,.2f} 亩" for r in sources["rows"]]
    return ToolResult(
        tool_call_id="",
        name="farmland_flow_summary",
        content="\n".join(content),
        artifacts=[
            _table(
                ["指标", "图斑数(个)", "面积(平方米)"],
                [dict(zip(["指标", "图斑数(个)", "面积(平方米)"], row)) for row in metrics],
                "farmland_metrics",
            ),
            _table(
                ["现三大类(模糊)", "图斑数(个)", "面积(平方米)"],
                [
                    {"现三大类(模糊)": r["category"], "图斑数(个)": r["n"], "面积(平方米)": round(float(r["area"]), 2)}
                    for r in flows["rows"]
                ],
                "farmland_outflow_by_category",
            ),
            _table(
                ["原三大类", "图斑数(个)", "面积(平方米)"],
                [
                    {"原三大类": r["category"], "图斑数(个)": r["n"], "面积(平方米)": round(float(r["area"]), 2)}
                    for r in sources["rows"]
                ],
                "farmland_inflow_source",
            ),
        ],
    )


@register_tool(
    "construction_change_summary",
    (
        "新增建设用地确定性汇总（后时项 TBLX 模糊映射）：总量与占全库比例、其中来自"
        "耕地的面积、分行政区前 N、以及来源构成（按原三大类）。用于“建设用地新增/"
        "来源构成/每行政区新增”类问题。"
    ),
    ConstructionChangeSummaryParams,
)
async def construction_change_summary(ctx: Any, top_n: int) -> ToolResult:
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return _no_gateway("construction_change_summary")
    table = context_table(ctx)
    const = _codes_in(category_codes("construction"))
    const_where = f'"TBLX" IN ({const})'
    try:
        total = await gateway.run_sql(
            f'SELECT count(*) AS n, sum("MJ") AS area FROM {table}',
            conversation_id="construction_change",
        )
        cur = await gateway.run_sql(
            f'SELECT count(*) AS n, sum("MJ") AS area FROM {table} WHERE {const_where}',
            conversation_id="construction_change",
        )
        crop = await gateway.run_sql(
            f'SELECT count(*) AS n, sum("MJ") AS area FROM {table} '
            f'WHERE {const_where} AND "DLBM" LIKE \'01%\'',
            conversation_id="construction_change",
        )
        counties = await gateway.run_sql(
            f'SELECT "XMC" AS region, count(*) AS n, sum("MJ") AS area FROM {table} '
            f"WHERE {const_where} GROUP BY \"XMC\" ORDER BY area DESC LIMIT {int(top_n)}",
            conversation_id="construction_change",
        )
        sources = await gateway.run_sql(
            f"SELECT {ORIG_CATEGORY_CASE} AS category, count(*) AS n, sum(t.\"MJ\") AS area "
            f"{_orig_summary_join(table)} WHERE {const_where} "
            "GROUP BY category ORDER BY area DESC",
            conversation_id="construction_change",
        )
    except Exception as exc:
        return _stat_error("construction_change_summary", exc)
    t = total["rows"][0] if total["rows"] else {"n": 0, "area": 0}
    c = cur["rows"][0] if cur["rows"] else {"n": 0, "area": 0}
    cp = crop["rows"][0] if crop["rows"] else {"n": 0, "area": 0}
    tn, ta = t["n"] or 0, float(t["area"] or 0)
    cn, ca = c["n"] or 0, float(c["area"] or 0)
    cpn, cpa = cp["n"] or 0, float(cp["area"] or 0)
    share = ca / ta * 100 if ta else 0.0
    content = [
        f"新增建设用地（变化后图斑类型 ∈ 建设模糊集）{cn:,} 个 / {m2_to_mu(ca):,.2f} 亩，"
        f"占全库面积 {share:.2f}%；其中原为耕地 {cpn:,} 个 / {m2_to_mu(cpa):,.2f} 亩。",
        "来源构成（按原三大类）：",
    ]
    content += [f"- {r['category']}: {r['n']:,} 个 / {m2_to_mu(r['area']):,.2f} 亩" for r in sources["rows"]]
    return ToolResult(
        tool_call_id="",
        name="construction_change_summary",
        content="\n".join(content),
        artifacts=[
            _table(
                ["指标", "数值"],
                [
                    ["新增建设用地图斑数(个)", cn],
                    ["新增建设用地面积(平方米)", round(ca, 2)],
                    ["占全库面积比例(%)", round(share, 2)],
                    ["其中原耕地面积(平方米)", round(cpa, 2)],
                ],
                "construction_metrics",
            ),
            _table(
                ["行政区", "图斑数(个)", "面积(平方米)"],
                [
                    {"行政区": r["region"], "图斑数(个)": r["n"], "面积(平方米)": round(float(r["area"]), 2)}
                    for r in counties["rows"]
                ],
                "construction_by_region",
            ),
            _table(
                ["来源(原三大类)", "图斑数(个)", "面积(平方米)"],
                [
                    {"来源(原三大类)": r["category"], "图斑数(个)": r["n"], "面积(平方米)": round(float(r["area"]), 2)}
                    for r in sources["rows"]
                ],
                "construction_source",
            ),
        ],
    )


@register_tool(
    "top_conversions",
    (
        "按面积排序的变化/转换类型 Top：原一级类（三调，经 DLBM）→ 现三大类"
        "（TBLX 模糊映射），例如 耕地→建设用地。用于“变化面积最大的转换类型”类问题。"
    ),
    TopConversionsParams,
)
async def top_conversions(ctx: Any, top_n: int) -> ToolResult:
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return _no_gateway("top_conversions")
    table = context_table(ctx)
    agri = _codes_in(category_codes("agricultural"))
    const = _codes_in(category_codes("construction"))
    cur_cat = (
        f'CASE WHEN t."TBLX" IN ({const}) THEN \'建设用地\' '
        f'WHEN t."TBLX" IN ({agri}) THEN \'农用地\' '
        f'WHEN t."TBLX" IN ({_codes_in(category_codes("unused"))}) THEN \'未利用地\' '
        "ELSE '其他' END"
    )
    try:
        rows = await gateway.run_sql(
            f'SELECT COALESCE(d."YJLMC", \'其他\') AS original, {cur_cat} AS current, '
            'count(*) AS n, sum(t."MJ") AS area '
            f"{_orig_summary_join(table)} "
            f"GROUP BY COALESCE(d.\"YJLMC\", '其他'), {cur_cat} "
            f"ORDER BY area DESC LIMIT {int(top_n)}",
            conversation_id="top_conversions",
        )
    except Exception as exc:
        return _stat_error("top_conversions", exc)
    items = rows["rows"]
    content = [f"变化面积最大的 {len(items)} 种转换（原一级类 → 现三大类，模糊映射）："]
    content += [
        f"- {r['original']}→{r['current']}: {r['n']:,} 个 / {m2_to_mu(r['area']):,.2f} 亩"
        for r in items
    ]
    return ToolResult(
        tool_call_id="",
        name="top_conversions",
        content="\n".join(content),
        artifacts=[
            _table(
                ["原一级类", "现三大类(模糊)", "图斑数(个)", "面积(平方米)"],
                [
                    {
                        "原一级类": r["original"],
                        "现三大类(模糊)": r["current"],
                        "图斑数(个)": r["n"],
                        "面积(平方米)": round(float(r["area"]), 2),
                    }
                    for r in items
                ],
                "top_conversions",
            )
        ],
    )


def _summary_lines(rows: list[dict[str, Any]], total_n: Any, total_area: float) -> list[str]:
    lines = [
        f"全库合计：共 {total_n:,} 个图斑、总面积 {m2_to_mu(total_area):,.2f} 亩。",
        f"共 {len(rows)} 种图斑类型：",
    ]
    for row in rows[:50]:
        lines.append(
            f"- {row['name']}：{row['n']:,} 个 / "
            f"{m2_to_mu(row['area_m2']):,.2f} 亩"
        )
    return lines


def _no_gateway(name: str) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        name=name,
        content="数据库未接入：请先配置 GEOAGENT_PG_DSN。",
        is_error=True,
    )


def _stat_error(name: str, exc: Exception) -> ToolResult:
    return ToolResult(
        tool_call_id="",
        name=name,
        content=f"统计失败: {type(exc).__name__}: {exc}",
        is_error=True,
    )


def get_stat_tools() -> list[Any]:
    return get_tools(
        "summarize_by_type",
        "fragment_stats",
        "farmland_flow_summary",
        "construction_change_summary",
        "top_conversions",
    )
