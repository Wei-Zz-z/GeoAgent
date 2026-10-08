"""土地变化监测快报统计与 Word（docx）生成（v1）。

统计基于 data."2026_1_change_landuse" 单表全量数据，口径与 SQLAgent 知识卡一致：
- 原土地类型 = "DLBM"/"DLMC"（三调二级类），图斑类型（变化后） = "TBLX"（影像）
- 面积 "MJ" 原始单位平方米，报告统一换算为亩
- TBLX → 三大类 使用 tools/categories.py 的临时推断映射（待业务替换）
- 疑似违法占地需要执法认定/管理信息套合数据，v1 留空
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt, RGBColor

from ..tools.categories import category_codes, display_groups

TABLE = 'data."2026_1_change_landuse"'
CROP_CODES = ("01", "1")
SEPARATE_CODES = ("DT", "TD", "WL", "TP", "QT")

# 排版字体约定（与用户模板《快报模板（删除统计数值）.doc》保持一致）。
FONT_TITLE = "方正小标宋简体"  # 大标题：二号
FONT_KAI = "楷体_GB2312"  # 标题下日期行：小二
FONT_FANGSONG = "仿宋_GB2312"  # 正文/表格：三号
FONT_HEI = "黑体"  # 章节标题、表题：三号，不加粗
FONT_LATIN = "Times New Roman"  # 西文/数字
HEADER_FILL = "D9D9D9"  # 表头浅灰底纹


def _codes_in(codes: list[str]) -> str:
    return ", ".join(f"'{c}'" for c in codes)


async def compute_briefing_stats(gw: Any, skills_dir: str | Path | None = None) -> dict[str, Any]:
    """按统计清单执行查询（全部走受控层，单表只读）。返回指标与表格数据。"""
    # 同一批查询只记录一次系统时间，后续正文、文件与图表共享，避免生成过程中漂移。
    query_started_at = datetime.now().astimezone()
    const_codes = _codes_in([c for c in category_codes("construction", skills_dir) if c not in SEPARATE_CODES])
    const_in = f'("TBLX" IN ({const_codes}))'
    crop_in = f'("TBLX" IN ({_codes_in(list(CROP_CODES))}))'
    restore_dlbm = (
        '("DLBM" LIKE \'02%\' OR "DLBM" LIKE \'03%\' OR "DLBM" LIKE \'04%\' '
        'OR "DLBM" IN (\'1104\', \'1104A\', \'1104K\'))'
    )

    async def scalar(sql: str) -> dict[str, Any]:
        rows = (await gw.run_sql(sql, conversation_id="briefing"))["rows"]
        return rows[0] if rows else {}

    stats: dict[str, Any] = {}
    stats["query_started_at"] = query_started_at.isoformat(timespec="seconds")
    meta = await scalar(
        f'SELECT min("QSX") AS qsx, max("HSX") AS hsx, '
        f'count(*) AS total_n, sum("MJ") AS total_area FROM {TABLE}'
    )
    stats["period"] = {"qsx": meta.get("qsx"), "hsx": meta.get("hsx")}
    stats["total_n"] = meta.get("total_n") or 0
    stats["total_area"] = float(meta.get("total_area") or 0)

    orig_crop = await scalar(
        f'SELECT count(*) AS n, sum("MJ") AS area FROM {TABLE} WHERE "DLBM" LIKE \'01%\''
    )
    stats["orig_crop"] = {"n": orig_crop.get("n") or 0, "area": float(orig_crop.get("area") or 0)}

    cur_crop = await scalar(f"SELECT count(*) AS n, sum(\"MJ\") AS area FROM {TABLE} WHERE {crop_in}")
    stats["cur_crop"] = {"n": cur_crop.get("n") or 0, "area": float(cur_crop.get("area") or 0)}

    crop2const = await scalar(
        f'SELECT count(*) AS n, sum("MJ") AS area FROM {TABLE} '
        f'WHERE "DLBM" LIKE \'01%\' AND {const_in}'
    )
    stats["crop2const"] = {
        "n": crop2const.get("n") or 0,
        "area": float(crop2const.get("area") or 0),
    }

    restore2crop = await scalar(
        f"SELECT count(*) AS n, sum(\"MJ\") AS area FROM {TABLE} "
        f"WHERE {restore_dlbm} AND {crop_in}"
    )
    stats["restore2crop"] = {
        "n": restore2crop.get("n") or 0,
        "area": float(restore2crop.get("area") or 0),
    }

    cur_const = await scalar(f"SELECT count(*) AS n, sum(\"MJ\") AS area FROM {TABLE} WHERE {const_in}")
    stats["cur_const"] = {"n": cur_const.get("n") or 0, "area": float(cur_const.get("area") or 0)}

    top_net = await gw.run_sql(
        "SELECT xmc, inflow, outflow, (inflow - outflow) AS net FROM ("
        f'SELECT "XMC" AS xmc, '
        f'COALESCE(SUM(CASE WHEN {crop_in} AND "DLBM" NOT LIKE \'01%\' THEN "MJ" END), 0) AS inflow, '
        f'COALESCE(SUM(CASE WHEN "DLBM" LIKE \'01%\' AND NOT {crop_in} THEN "MJ" END), 0) AS outflow '
        f"FROM {TABLE} GROUP BY \"XMC\") s WHERE inflow < outflow ORDER BY net ASC LIMIT 10",
        conversation_id="briefing",
    )
    stats["top_net_counties"] = [
        {
            "xmc": r["xmc"],
            "outflow": float(r["outflow"]),
            "inflow": float(r["inflow"]),
            "net": float(r["net"]),
        }
        for r in top_net["rows"]
    ]

    top_const = await gw.run_sql(
        f'SELECT "XMC" AS xmc, count(*) AS n, sum("MJ") AS area FROM {TABLE} '
        f"WHERE {const_in} GROUP BY \"XMC\" ORDER BY area DESC LIMIT 10",
        conversation_id="briefing",
    )
    stats["top_const_counties"] = [
        {"xmc": r["xmc"], "n": r["n"], "area": float(r["area"])} for r in top_const["rows"]
    ]
    groups = display_groups(skills_dir)
    stats["categories_display"] = [(label, [item for item in items if item[0] not in SEPARATE_CODES]) for label, items in groups if label != "单列类型（不计入三大类）"]
    stats["categories_display"].append(("单列类型（不计入三大类）", [item for _, items in groups for item in items if item[0] in SEPARATE_CODES]))
    separate = await gw.run_sql(
        f'SELECT "TBLX" AS code, sum("MJ") AS area FROM {TABLE} '
        f'WHERE "TBLX" IN ({_codes_in(list(SEPARATE_CODES))}) GROUP BY "TBLX" ORDER BY area DESC',
        conversation_id="briefing",
    )
    stats["separate_types"] = [{"code": r["code"], "area": float(r["area"] or 0)} for r in separate["rows"]]
    types = await gw.run_sql(f'SELECT "TBLX" AS code, sum("MJ") AS area FROM {TABLE} GROUP BY "TBLX" ORDER BY area DESC LIMIT 10', conversation_id="briefing")
    stats["top_types"] = [{"code": r["code"], "area": float(r["area"] or 0)} for r in types["rows"]]

    stats["crop_net_n"] = stats["cur_crop"]["n"] - stats["orig_crop"]["n"]
    stats["crop_net_area"] = stats["cur_crop"]["area"] - stats["orig_crop"]["area"]
    if stats["cur_const"]["area"] > 0:
        stats["crop2const_pct"] = stats["crop2const"]["area"] / stats["cur_const"]["area"] * 100
    else:
        stats["crop2const_pct"] = 0.0
    return stats


def _fmt(n: float) -> str:
    """只在展示端换算，原始统计继续保持平方米；1平方米=0.0015亩。"""
    return f"{n * 0.0015:.2f}"


def _rpr(run: Any) -> Any:
    """获取 run 的 rPr 元素（不存在则创建）。"""
    return run._element.get_or_add_rPr()


def _set_run_font(
    run: Any,
    *,
    east_asia: str = FONT_FANGSONG,
    ascii_font: str = FONT_LATIN,
    size: float = 16,
    bold: bool = False,
) -> None:
    """按模板统一设置 run 字体（西文与中文分别指定，中文一律黑色）。"""
    run.font.name = ascii_font
    rPr = _rpr(run)
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), ascii_font)
    rFonts.set(qn("w:hAnsi"), ascii_font)
    rFonts.set(qn("w:eastAsia"), east_asia)
    rFonts.set(qn("w:cs"), ascii_font)

    run.font.size = Pt(size)
    sz = rPr.find(qn("w:sz"))
    szCs = rPr.find(qn("w:szCs"))
    if szCs is None:
        szCs = OxmlElement("w:szCs")
        if sz is not None:
            sz.addnext(szCs)
        else:
            rPr.append(szCs)
    szCs.set(qn("w:val"), str(int(size * 2)))

    run.font.color.rgb = RGBColor(0, 0, 0)
    if bold:
        run.font.bold = True
        b = rPr.find(qn("w:b"))
        bCs = rPr.find(qn("w:bCs"))
        if bCs is None:
            bCs = OxmlElement("w:bCs")
            if b is not None:
                b.addnext(bCs)
            else:
                rPr.append(bCs)


def _set_first_line_indent(p: Any, chars: int = 2, size_pt: float = 16) -> None:
    """首行缩进：同时写磅值与“字符”单位，保证 Word 里按中文字符缩进。"""
    p.paragraph_format.first_line_indent = Pt(size_pt * chars)
    ind = p._p.get_or_add_pPr().find(qn("w:ind"))
    if ind is not None:
        ind.set(qn("w:firstLineChars"), str(int(chars * 100)))


def _set_before_lines(p: Any, lines: float) -> None:
    """段前间距按“行”设置（模板中 1 行对应 312 twips/15.6pt）。"""
    p.paragraph_format.space_before = Pt(15.6 * lines)
    spacing = p._p.get_or_add_pPr().find(qn("w:spacing"))
    if spacing is not None:
        spacing.set(qn("w:beforeLines"), str(int(lines * 100)))


def _setup_page(doc: Document) -> None:
    """A4 竖版，左右 3cm、上下 2.54cm（与模板一致）。"""
    sec = doc.sections[0]
    sec.page_width = Mm(210)
    sec.page_height = Mm(297)
    sec.left_margin = Cm(3)
    sec.right_margin = Cm(3)
    sec.top_margin = Cm(2.54)
    sec.bottom_margin = Cm(2.54)
    sec.header_distance = Mm(15)
    sec.footer_distance = Mm(17.5)


def _setup_normal_style(doc: Document) -> None:
    """Normal 基线：正文三号仿宋_GB2312 + Times New Roman。"""
    style = doc.styles["Normal"]
    style.font.name = FONT_LATIN
    style.font.size = Pt(16)
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:eastAsia"), FONT_FANGSONG)
    style.paragraph_format.space_after = Pt(0)


def _new_paragraph(
    doc: Document,
    *,
    align: WD_ALIGN_PARAGRAPH | None = None,
    indent_chars: int = 0,
    indent_size_pt: float = 16,
    line_multiple: float | None = None,
    line_exact_pt: float | None = None,
    space_before: float = 0,
    space_after: float = 0,
) -> Any:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    if align is not None:
        pf.alignment = align
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if line_multiple is not None:
        pf.line_spacing = line_multiple
    if line_exact_pt is not None:
        pf.line_spacing = Pt(line_exact_pt)
    if indent_chars:
        _set_first_line_indent(p, chars=indent_chars, size_pt=indent_size_pt)
    return p


def _add_title(doc: Document, text: str) -> None:
    """大标题：方正小标宋简体二号居中，固定行距 32 磅。"""
    p = _new_paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER, line_exact_pt=32)
    run = p.add_run(text)
    _set_run_font(run, east_asia=FONT_TITLE, ascii_font=FONT_TITLE, size=22)


def _add_title_meta(doc: Document, text: str) -> None:
    """标题下日期行：楷体_GB2312 小二居中，固定行距 32 磅。"""
    p = _new_paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER, line_exact_pt=32)
    run = p.add_run(text)
    _set_run_font(run, east_asia=FONT_KAI, size=20)


def _add_body(doc: Document, text: str) -> None:
    """正文：三号仿宋_GB2312，两端对齐、首行缩进 2 字符、1.5 倍行距。"""
    p = _new_paragraph(
        doc,
        align=WD_ALIGN_PARAGRAPH.JUSTIFY,
        indent_chars=2,
        line_multiple=1.5,
    )
    run = p.add_run(text)
    _set_run_font(run, east_asia=FONT_FANGSONG, size=16)


def _add_section_heading(doc: Document, text: str, *, first: bool = False) -> None:
    """章节标题：黑体三号不加粗，首行缩进 2 字符；后续章节段前空 1 行。"""
    p = _new_paragraph(
        doc,
        align=WD_ALIGN_PARAGRAPH.JUSTIFY,
        indent_chars=2,
        line_multiple=1.5,
        space_before=0 if first else 15.6,
    )
    if not first:
        _set_before_lines(p, 1)
    # 保留大纲级别，便于 Word 导航（标题本身用黑体不加粗，样式不套用 Heading）。
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), "0")
    p._p.get_or_add_pPr().append(outline)
    run = p.add_run(text)
    _set_run_font(run, east_asia=FONT_HEI, ascii_font=FONT_HEI, size=16)


def _add_table_caption(doc: Document, text: str) -> None:
    """表题：黑体三号居中，不加粗。"""
    p = _new_paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    run = p.add_run(text)
    _set_run_font(run, east_asia=FONT_HEI, ascii_font=FONT_HEI, size=16)


def _add_report_chart(
    doc: Document,
    number: int,
    title: str,
    rows: list[dict[str, Any]],
    label_key: str,
    value_key: str,
) -> None:
    """在相关正文附近插入图表，并统一图题、单位和字体。"""
    if not rows:
        return
    from .charts import add_bar_chart
    from ..tools.labels import TBLX_LABELS

    # 各图使用稳定且可区分的配色；图2为净减少，使用橙红色强调负向变化。
    chart_colors = {
        1: ("4472C4", "2F5597"),
        2: ("ED7D31", "C65911"),
        3: ("70AD47", "548235"),
        4: ("8064A2", "5F497A"),
    }
    fill_color, line_color = chart_colors.get(number, chart_colors[1])
    result = add_bar_chart(
        doc,
        f"图{number}　{title}",
        [TBLX_LABELS.get(r[label_key], r[label_key]) for r in rows],
        [float(r[value_key]) * 0.0015 for r in rows],
        fill_color=fill_color,
        line_color=line_color,
    )
    if result is None:
        return
    chart_paragraph, caption = result
    chart_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption.paragraph_format.space_before = Pt(3)
    caption.paragraph_format.space_after = Pt(6)
    for run in caption.runs:
        _set_run_font(run, east_asia=FONT_FANGSONG, size=14)


def _insert_ordered(parent: Any, child: Any, order: list[str]) -> None:
    """按 OOXML 子元素顺序插入 child（先移除同名旧元素）。"""
    for existing in list(parent):
        if existing.tag == child.tag:
            parent.remove(existing)
    tag = child.tag
    try:
        idx = order.index(tag)
    except ValueError:
        idx = len(order)
    for el in parent:
        if el.tag in order and order.index(el.tag) > idx:
            el.addprevious(child)
            return
    parent.append(child)


_TCPR_ORDER = [
    qn("w:tcW"),
    qn("w:gridSpan"),
    qn("w:hMerge"),
    qn("w:vMerge"),
    qn("w:tcBorders"),
    qn("w:shd"),
    qn("w:noWrap"),
    qn("w:tcMar"),
    qn("w:textDirection"),
    qn("w:tcFitText"),
    qn("w:vAlign"),
]


def _shade_cell(cell: Any, fill: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    _insert_ordered(tcPr, shd, _TCPR_ORDER)


def _set_table_borders(table: Any) -> None:
    """全表单线黑色边框（与模板正文表格一致，1pt）。"""
    tblPr = table._tbl.tblPr
    old = tblPr.find(qn("w:tblBorders"))
    if old is not None:
        tblPr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for tag in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{tag}")
        edge.set(qn("w:val"), "single")
        edge.set(qn("w:sz"), "8")
        edge.set(qn("w:space"), "0")
        edge.set(qn("w:color"), "000000")
        borders.append(edge)
    anchor = tblPr.find(qn("w:tblLayout"))
    if anchor is None:
        anchor = tblPr.find(qn("w:tblLook"))
    if anchor is not None:
        anchor.addprevious(borders)
    else:
        tblPr.append(borders)


def _set_table_widths(table: Any, widths_cm: list[float]) -> None:
    """固定列宽（cm），并保持表格居中。"""
    table.autofit = False
    tblPr = table._tbl.tblPr
    tblLayout = tblPr.find(qn("w:tblLayout"))
    if tblLayout is None:
        tblLayout = OxmlElement("w:tblLayout")
        tblLayout.set(qn("w:type"), "fixed")
        anchor = tblPr.find(qn("w:tblLook"))
        if anchor is not None:
            anchor.addprevious(tblLayout)
        else:
            tblPr.append(tblLayout)
    grid = table._tbl.find(qn("w:tblGrid"))
    grid_cols = grid.findall(qn("w:gridCol"))
    for gc, width_cm in zip(grid_cols, widths_cm):
        gc.set(qn("w:w"), str(int(round(width_cm * 567))))
    for row in table.rows:
        for cell, width_cm in zip(row.cells, widths_cm):
            cell.width = Cm(width_cm)


def _set_header_row(row: Any) -> None:
    """表头行：跨页重复，最小行高与模板一致。"""
    trPr = row._tr.get_or_add_trPr()
    if trPr.find(qn("w:tblHeader")) is None:
        trPr.append(OxmlElement("w:tblHeader"))
    row.height = Mm(7.5)
    from docx.enum.table import WD_ROW_HEIGHT_RULE

    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST


def _fill_cell(cell: Any, text: Any, *, bold: bool) -> None:
    """单元格文字：三号仿宋_GB2312 居中，表头加粗。"""
    p = cell.paragraphs[0]
    pf = p.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    run = p.add_run(str(text))
    _set_run_font(run, east_asia=FONT_FANGSONG, size=16, bold=bold)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def _add_table(
    doc: Document,
    headers: list[str],
    rows: list[list[Any]],
    widths_cm: list[float],
) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table)
    _set_table_widths(table, widths_cm)
    _set_header_row(table.rows[0])
    for cell, header in zip(table.rows[0].cells, headers):
        _shade_cell(cell, HEADER_FILL)
        _fill_cell(cell, header, bold=True)
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            _fill_cell(cell, value, bold=False)


def build_briefing_docx(stats: dict[str, Any], out_path: Path) -> Path:
    """按模板排版要求生成 Word 快报（字体/段落格式对齐用户模板）。"""
    doc = Document()
    _setup_page(doc)
    _setup_normal_style(doc)

    # 标题区：顶部空行 + 大标题 + 日期/生成信息行 + 正文前空行（同模板版式）。
    _new_paragraph(doc, line_exact_pt=32)
    _add_title(doc, "主要地类变化监测快报")
    period = stats.get("period") or {}
    qsx = period.get("qsx") or "—"
    hsx = period.get("hsx") or "—"
    query_started = stats.get("query_started_at")
    try:
        generated_at = datetime.fromisoformat(query_started) if query_started else datetime.now().astimezone()
    except (TypeError, ValueError):
        generated_at = datetime.now().astimezone()
    _add_title_meta(doc, f"监测期：{qsx} 至 {hsx}（2026年第一期）")
    _add_title_meta(doc, f"查询时间：{generated_at:%Y-%m-%d %H:%M}")
    _new_paragraph(doc, indent_chars=2, line_exact_pt=29)

    _add_section_heading(doc, "一、总体情况", first=True)
    _add_body(
        doc,
        f"本期通过遥感影像与国土变更调查数据对比，共提取主要地类新发生变化图斑 "
        f"{stats['total_n']} 个，总面积 {_fmt(stats['total_area'])} 亩。",
    )
    _add_report_chart(doc, 1, "变化后图斑类型面积前10位", stats.get("top_types", []), "code", "area")

    _add_section_heading(doc, "二、耕地变化情况")
    oc, cc = stats["orig_crop"], stats["cur_crop"]
    _add_body(
        doc,
        f"变化前为耕地的面积 {_fmt(oc['area'])} 亩；"
        f"变化后为耕地的面积 {_fmt(cc['area'])} 亩；"
        f"耕地净变化（变化后−变化前）为 {_fmt(stats['crop_net_area'])} 亩，"
        + ("呈净减少态势。" if stats['crop_net_area'] < 0 else "呈净增加态势。" if stats['crop_net_area'] > 0 else "总体持平。"),
    )
    c2c, r2c = stats["crop2const"], stats["restore2crop"]
    _add_body(
        doc,
        f"原耕地流向建设用地的面积 {_fmt(c2c['area'])} 亩；由园地、林地、草地、坑塘等"
        f"转为耕地的面积 {_fmt(r2c['area'])} 亩（不等同于按种植属性认定的恢复性地类）。",
    )
    _add_table_caption(doc, "表1　耕地变化总体情况")
    _add_table(
        doc,
        ["指标", "面积（亩）"],
        [
            ["变化前为耕地（原）", _fmt(oc["area"])],
            ["变化后为耕地（现）", _fmt(cc["area"])],
            ["耕地净变化（现−原）", _fmt(stats["crop_net_area"])],
            ["其中：原耕地流向建设用地", _fmt(c2c["area"])],
            ["其中：园林草坑塘流入耕地", _fmt(r2c["area"])],
        ],
        [10, 5],
    )
    _add_table_caption(doc, "表2　耕地净减少最多的10个县（市、区）")
    _add_table(
        doc,
        ["县（市、区）", "流出面积（亩）", "流入面积（亩）", "净变化面积（亩）"],
        [
            [r["xmc"], _fmt(r["outflow"]), _fmt(r["inflow"]), _fmt(r["net"])]
            for r in stats["top_net_counties"]
        ],
        [3.7, 3.8, 3.7, 3.8],
    )
    _add_report_chart(doc, 2, "耕地净减少县域面积排名", stats.get("top_net_counties", []), "xmc", "net")

    _add_section_heading(doc, "三、新增建设用地情况")
    ccst = stats["cur_const"]
    _add_body(
        doc,
        f"变化后图斑类型为建设用地的面积 {_fmt(ccst['area'])} 亩；"
        f"其中原为耕地的面积 {_fmt(c2c['area'])} 亩，"
        f"占新增建设用地（变化后）面积的 {stats['crop2const_pct']:.2f}%。",
    )
    _add_table_caption(doc, "表3　新增建设用地（变化后）面积前10的县（市、区）")
    _add_table(
        doc,
        ["县（市、区）", "面积（亩）"],
        [[r["xmc"], _fmt(r["area"])] for r in stats["top_const_counties"]],
        [10, 5],
    )
    _add_report_chart(doc, 3, "变化后建设用地县域面积排名", stats.get("top_const_counties", []), "xmc", "area")

    _add_section_heading(doc, "四、疑似违法占地情况")
    _add_body(
        doc,
        "本版暂未接入违法认定及用地管理信息数据（农转用、供地、增减挂钩、采矿权、"
        "设施农用地/临时用地审批等），相关统计留空，待数据接入后补充。",
    )

    _add_section_heading(doc, "五、统计说明")
    for line in [
        "原土地类型＝DLBM/DLMC（三调二级类）；图斑类型（变化后）＝TBLX（影像识别）。",
        "面积统一以亩展示，按1平方米=0.0015亩换算，保留两位小数，数字不加千分位逗号。",
        "动土、推堆土、瓦砾、推平、其他单列统计，不计入三大类；耕地流向这些类型仍计入耕地流出。",
        "TBLX→三大类映射为临时推断版本，正式映射下发后将更新口径。",
        "统计范围为数据库表全量数据。",
    ]:
        _add_body(doc, line)

    if stats.get("separate_types"):
        from ..tools.labels import TBLX_LABELS
        _add_table_caption(doc, "动土等其他类型单列统计")
        _add_table(doc, ["类型", "面积（亩）"],
                   [[TBLX_LABELS.get(r["code"], r["code"]), _fmt(r["area"])] for r in stats["separate_types"]],
                   [10, 5])
        _add_report_chart(doc, 4, "动土等其他类型单列面积", stats["separate_types"], "code", "area")

    if stats.get("categories_display"):
        _add_section_heading(doc, "附录　图斑类型→三大类（临时映射）")
        for label, items in stats["categories_display"]:
            _add_body(
                doc,
                label
                + "："
                + "、".join(
                    f"{name}({code})" + ("（待确认）" if uncertain else "")
                    for code, name, uncertain in items
                ),
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
