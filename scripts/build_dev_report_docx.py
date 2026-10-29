"""生成面向项目组的简版 Word 开发报告。"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "开发报告-智能问数优化-简版.docx"

FONT = "微软雅黑"
NAVY = RGBColor(0x1F, 0x3A, 0x5F)
BLUE = RGBColor(0x2E, 0x5C, 0x8A)
GREY = RGBColor(0x5B, 0x67, 0x75)
LIGHT = "EEF4FA"
WHITE = "FFFFFF"


def set_run_font(run, size: float = 10.5, bold: bool = False, color=RGBColor(0x22, 0x2A, 0x33)) -> None:
    run.font.name = FONT
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = color
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT)


def style_font(style, size: float, bold: bool = False, color=RGBColor(0x22, 0x2A, 0x33)) -> None:
    style.font.name = FONT
    style.font.size = Pt(size)
    style.bold = bold
    style.font.color.rgb = color
    style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT)


def shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def cell_margins(cell, top=80, bottom=80, left=120, right=120) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for tag, value in (("top", top), ("bottom", bottom), ("start", left), ("end", right)):
        el = OxmlElement(f"w:{tag}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tc_pr.append(mar)


def set_cell_text(cell, text: str, size: float = 9.2, bold: bool = False, color=RGBColor(0x22, 0x2A, 0x33)) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.15
    set_run_font(p.add_run(text), size=size, bold=bold, color=color)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    cell_margins(cell)


def add_table(doc, headers, rows, widths, size=9.0) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.allow_autofit = False
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.width = Cm(widths[index])
        set_cell_text(cell, header, size=size, bold=True, color=NAVY)
        shade(cell, "DCE8F5")
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)
    for row_data in rows:
        row = table.add_row()
        for index, value in enumerate(row_data):
            cell = row.cells[index]
            cell.width = Cm(widths[index])
            set_cell_text(cell, str(value), size=size)
        row.cells[0].paragraphs[0].runs[0].bold = True
    # 避免表格跨页断行
    for row in table.rows:
        tr_pr = row._tr.get_or_add_trPr()
        cant_split = OxmlElement("w:cantSplit")
        tr_pr.append(cant_split)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def heading(doc, text: str, level: int = 1) -> None:
    p = doc.add_paragraph(style=f"Heading {level}")
    p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    p.paragraph_format.space_after = Pt(5 if level == 1 else 3)
    p.paragraph_format.keep_with_next = True
    set_run_font(p.add_run(text), size=14 if level == 1 else 11.5, bold=True, color=NAVY)


def body(doc, text: str, size: float = 10.2, bold_prefix: str = "") -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.35
    if bold_prefix and text.startswith(bold_prefix):
        set_run_font(p.add_run(bold_prefix), size=size, bold=True, color=BLUE)
        set_run_font(p.add_run(text[len(bold_prefix):]), size=size)
    else:
        set_run_font(p.add_run(text), size=size)


def bullet(doc, text: str, size: float = 10.0) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.3
    set_run_font(p.add_run(text), size=size)


def callout(doc, title: str, lines: list[str], fill: str = "F3F7FB") -> None:
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    cell = table.rows[0].cells[0]
    cell.width = Cm(17.0)
    shade(cell, fill)
    cell_margins(cell, top=140, bottom=120, left=180, right=180)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(4)
    set_run_font(p.add_run(title), size=10.5, bold=True, color=NAVY)
    for line in lines:
        p = cell.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.3
        set_run_font(p.add_run(line), size=9.8)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    set_run_font(run, size=8.5, color=GREY)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    r.append(t)
    fld.append(r)
    paragraph._p.append(fld)


def main() -> None:
    doc = Document()
    section = doc.sections[0]
    section.page_height = Cm(29.7)
    section.page_width = Cm(21.0)
    section.top_margin = Cm(1.75)
    section.bottom_margin = Cm(1.65)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)
    section.header_distance = Cm(0.8)
    section.footer_distance = Cm(0.8)

    normal = doc.styles["Normal"]
    style_font(normal, 10.2)
    normal.paragraph_format.line_spacing = 1.35

    for name, size in (("Heading 1", 14), ("Heading 2", 11.5)):
        style_font(doc.styles[name], size, bold=True, color=NAVY)

    # 页眉
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_run_font(header.add_run("GeoAgent · 智能问数优化（简版）"), size=8, color=GREY)

    # 页脚
    footer = section.footer.paragraphs[0]
    add_page_number(footer)

    # 标题
    title = doc.add_paragraph()
    title.paragraph_format.space_before = Pt(2)
    title.paragraph_format.space_after = Pt(2)
    set_run_font(title.add_run("GeoAgent 智能问数优化开发报告"), size=21, bold=True, color=NAVY)
    subtitle = doc.add_paragraph()
    subtitle.paragraph_format.space_after = Pt(10)
    set_run_font(subtitle.add_run("语义匹配、结果校验与数据口径治理（简版）"), size=12, color=BLUE)

    meta = doc.add_table(rows=2, cols=4)
    meta.style = "Table Grid"
    meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta.autofit = False
    meta_values = [
        ("汇报对象", "项目组同事", "版本", "main（最新提交）"),
        ("日期", "2026-10-29", "数据范围", "2026 年第一、二期"),
    ]
    for r_index, row_data in enumerate(meta_values):
        for c_index, value in enumerate(row_data):
            cell = meta.rows[r_index].cells[c_index]
            cell.width = Cm(2.5 if c_index % 2 == 0 else 6.0)
            set_cell_text(cell, value, size=8.8, bold=(c_index % 2 == 0), color=NAVY if c_index % 2 == 0 else RGBColor(0x22, 0x2A, 0x33))
            if c_index % 2 == 0:
                shade(cell, LIGHT)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)

    heading(doc, "一、一页看懂")
    callout(
        doc,
        "这次改动的核心",
        [
            "原系统：问法靠字面匹配，没命中就静默转自由 SQL；数字、单位、口径缺少校验，多轮不连续。",
            "现在：先理解问题 → 选标准口径 → 执行 → 校验数字 → 整理版式；不确定时给候选/澄清，不硬答。",
            "效果：留一别名自动执行率 10.5% → 93.0%，Top-1 100%，误执行 0；golden 7/7。",
        ],
    )

    heading(doc, "二、原系统存在的问题")
    problems = [
        ("换说法就掉线：", "只认别名/关键词/整句相似度，口语化问法容易落到自由 SQL。"),
        ("未命中静默降级：", "没有候选/澄清，模型可能自己挑表、定口径，把某县按全库统计。"),
        ("数字不可溯源：", "自由 SQL 正文可能把平方米当亩、把亩乘 1.5，或编造工具结果里没有的数字。"),
        ("多轮不连续：", "只支持“那第二期呢”等固定说法，换一种指代就要用户重说完整问题。"),
        ("数据口径不完整：", "验证期次可能混入生产，TBLX 缺正式来源，TZ/QSX/CZCSXM 字段显示裸编码。"),
        ("回答像开发文档：", "出现 Markdown 标题、“动土(DT)”字段编码、内部表名和实现说明。"),
    ]
    for prefix, text in problems:
        bullet(doc, prefix + text)

    heading(doc, "三、现版本改了什么")
    add_table(
        doc,
        ["模块", "改动", "用户能感知的效果"],
        [
            ("语义匹配", "槽位理解 + 向量召回 + RRF + p(correct) 校准 + 灰度区 LLM judge", "换种说法也能命中；不确定先列候选"),
            ("结果校验", "空结果、负值、比例范围、净变化、分项合计、区县筛选", "可疑结果不当作结论"),
            ("数字校验", "claim-level：可修正的修正，不能修正的用证据重写，仍失败则阻断正文", "错单位/编造数字不再进正文"),
            ("多轮追问", "规则优先；短追问走结构化状态抽取，保留 QueryState", "“那第二期呢”能继承上一轮"),
            ("数据治理", "2026_3 隐藏；TBLX 33 码对齐；TZ/QSX/CZCSXM 字典；版本元数据", "期次干净、表格可读、可追溯"),
            ("回答版式", "去掉标题/字段编码/内部表名/实现说明，先结论后口径", "像业务回答，方便直接汇报"),
            ("回答反馈", "曾实现，按项目要求完整移除", "页面更简洁，不收集反馈"),
        ],
        widths=[2.5, 9.0, 5.3],
    )

    heading(doc, "四、改造前后对比")
    add_table(
        doc,
        ["维度", "改造前", "当前版本"],
        [
            ("问题理解", "整句字面/别名/Jaccard", "主体、时项、聚合、维度、限定、方向、风险槽位"),
            ("没见过的问法", "容易掉自由 SQL", "向量召回 + 槽位否决 + RRF"),
            ("未命中", "静默转自由 SQL", "候选 / 澄清 / 带“理解计划”的 fallback"),
            ("正文数字", "无逐条核对", "claim-level 修正/重写/阻断"),
            ("单位", "平方米/亩容易混", "展示层统一亩，1 m² = 0.0015 亩"),
            ("多轮", "固定几类说法", "规则 + 结构化状态抽取"),
            ("期次/字段", "验证期次可能混入；显示 ZZ/203", "2026_3 隐藏；显示住宅/村庄/日期"),
            ("可评估性", "人工试问", "留一别名/留出/锁定集 + 审计 JSONL"),
        ],
        widths=[2.8, 6.2, 7.8],
    )

    heading(doc, "五、示例问题与效果")
    examples = [
        (
            "标准总览",
            "全库共有多少个变化图斑、总面积是多少？",
            "164,798 个图斑 / 30,879.18 亩；18 种类型；附口径、期次、单位。",
        ),
        (
            "期次与多轮",
            "第二期有多少图斑、总面积是多少？→ 再按类型分",
            "27,722 个 / 1,838,583.37 亩；追问沿用第二期，不重复完整问题。",
        ),
        (
            "模糊问题",
            "新增建设用地情况怎么样？",
            "不直接查库；列 construction_change / ratio / top10 三个候选，等用户确认。",
        ),
        (
            "标准库不支持的口径",
            "第二期图斑面积超过 1000 亩的有几个？",
            "转自由 SQL；对无法溯源的阈值描述做重写，不把错误数字当结论。",
        ),
    ]
    for tag, question, effect in examples:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.keep_with_next = True
        set_run_font(p.add_run(f"【{tag}】"), size=9.5, bold=True, color=BLUE)
        set_run_font(p.add_run(question), size=9.8, bold=True)
        body(doc, effect, size=9.4)

    callout(
        doc,
        "示例数值说明",
        [
            "以上为本机 geoagent_data 的实测结果；换环境/换期次数据量会变，但回答形态和口径行为一致。",
            "标准总览、第二期、多轮追问为真实冒烟；候选/阈值示例用于说明决策方式。",
        ],
        fill="FAF7EE",
    )

    heading(doc, "六、这段时间完成的任务")
    add_table(
        doc,
        ["任务", "状态", "落点"],
        [
            ("claim-level 数字校验：可修正/重写/阻断", "完成", "number_guard.py / agents/sql.py"),
            ("多轮从模式匹配升级为结构化状态抽取", "完成", "dialogue.py / agents/sql.py"),
            ("隐藏 2026_3 验证期次", "完成", "datasets.json / datasets.py"),
            ("TBLX 映射改为字典驱动", "完成，6 个边界项待业务确认", "tblx_categories.json"),
            ("补 TZ/QSX/CZCSXM 业务字典", "完成，未确认值保留原码", "field_dictionaries.json"),
            ("数据版本/口径治理", "完成，版本元数据保留", "data_versions.json"),
            ("回答反馈与审计回放", "已按需求完整移除", "无按钮/接口/表/脚本"),
            ("回归、文档、GitHub 提交", "完成", "docs / backend tests"),
        ],
        widths=[8.5, 4.2, 4.3],
    )

    heading(doc, "七、验证结果与已知限制")
    body(doc, "验证结果", bold_prefix="验证结果")
    bullet(doc, "pytest / 前端构建：352 个用例，347 passed / 5 个已知本机环境失败；前端构建通过。")
    bullet(doc, "golden SQL：7 / 7 通过。")
    bullet(doc, "真实冒烟：标准总览 164,798 个图斑 / 30,879.18 亩；/api/datasets 仅 2026_1、2026_2。")
    body(doc, "已知限制（需要下一轮业务确认）", bold_prefix="已知限制")
    for prefix, text in [
        ("TBLX 6 个边界类型：", "农村道路、盐田、光伏、公园绿地、填海项目、围海项目仍标记 pending_business，不冒充正式规则。"),
        ("TZ 部分编码：", "SN/LC 和数字编码含义未确认；未确认值保留原码，不猜。"),
        ("claim-level 校验：", "复杂多步汇总可能触发阻断，这是安全取舍；阻断时保留查询表格。"),
        ("5 个测试失败：", "均为本机 Windows 临时目录/旧 mock/养老场景开关问题，改动前同样失败，不是本轮功能回归。"),
    ]:
        bullet(doc, prefix + text, size=9.6)

    heading(doc, "八、汇报时可以直接这样说")
    callout(
        doc,
        "30 秒汇报口径",
        [
            "“这次主要解决三件事：问法能听懂、口径能选对、数字能校验。换种说法不再直接掉自由 SQL，拿不准时先给候选。”",
            "“离线留一别名自动执行率从 10.5% 提到 93%，Top-1 100%，误执行 0；golden SQL 7/7。”",
            "“同时把验证期次隐藏、字段字典补齐、回答版式清理；TBLX 的 6 个边界类型还需业务确认。”",
        ],
        fill="EDF6F1",
    )

    heading(doc, "九、交接位置")
    add_table(
        doc,
        ["内容", "位置"],
        [
            ("本报告（详细版）", "docs/开发报告-智能问数优化.md"),
            ("完整技术说明", "docs/智能问数语义匹配优化落地说明.md（2.4 节为最终改动索引）"),
            ("语义匹配/校验/多轮", "backend/geoagent/qa_semantics.py、number_guard.py、dialogue.py"),
            ("TBLX / 字段字典", "skills/land-report/assets/tblx_categories.json、skills/qa-library/assets/field_dictionaries.json"),
            ("GitHub", "github.com/timelesssun99/GeoAgent（main）"),
        ],
        widths=[4.2, 12.6],
    )

    # 文末一句
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(p.add_run("一句话总结：让问答从“模型自由发挥”变成“理解 → 选口径 → 执行 → 校验”的可检查链路。"), size=9.5, bold=True, color=BLUE)

    props = doc.core_properties
    props.title = "GeoAgent 智能问数优化开发报告（简版）"
    props.subject = "语义匹配、结果校验与数据口径治理"
    props.author = "GeoAgent 项目组"
    props.keywords = "GeoAgent, 智能问数, 语义匹配, 数据治理, 开发报告"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
