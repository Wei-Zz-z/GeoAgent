"""查询结果展示用的中文字段映射。

run_sql 返回的表格在展示与摘要时，把英文/缩写字段名映射为中文表头，
并把图斑类型（TBLX）的编码/字母值映射为中文名称（如 DT -> 动土）。

映射来源：
- 数据库注释（pg_description）与 knowledge_base.metadata_column 中
  data.2026_1_change_landuse 的 23 个字段注释（2026-09 核验一致）；
- knowledge_base.dict_tblx（33 类图斑类型）；数据中编码 1-4 存为前导零
  01-04，映射表同时收录两种写法。

业务口径说明：DLBM/DLMC 在库中注释为"地类编码/名称"，按业务口径为变化前的
原土地类型，展示为"原地类编码/原地类名称"；SFYN/SFHX 库中简写为"永农/红线"，
展示时展开为"永久基本农田/生态保护红线"。

注意：若数据库字典更新，需同步本文件与 SQLAgent 知识卡。
"""

from __future__ import annotations

from typing import Any

from .units import area_header, is_area_column, m2_to_mu


COLUMN_LABELS: dict[str, str] = {
    "OBJECTID": "主键",
    "XZQDM": "县级行政区代码",
    "XMC": "县级行政区名称",
    "TBLX": "图斑类型",
    "TZ": "图斑特征",
    "QSX": "前时相",
    "HSX": "后时相",
    "DDTC": "单独图层名称",
    "BZ": "备注",
    "JSYDXZ": "建设用地性质",
    "JCBH": "图斑编号",
    "DLBM": "原地类编码",
    "DLMC": "原地类名称",
    "TBXHMC": "图斑细化名称",
    "ZZSXDM": "种植属性代码",
    "ZZSXMC": "种植属性名称",
    "CZCSXM": "城镇村属性码",
    "TBXHDM": "图斑细化代码",
    "SFYN": "是否涉及永久基本农田",
    "SFHX": "是否涉及生态保护红线",
    "MJ": "面积(亩)",
    "CREATE_AT": "记录创建时间",
    "UPDATE_AT": "记录最后修改时间",
}


TBLX_LABELS: dict[str, str] = {
    "20": "建/构筑物",
    "YH": "硬化",
    "DT": "动土",
    "TD": "推堆土",
    "DL1": "建成道路",
    "DL2": "在建道路",
    "DL3": "路网",
    "TL": "铁路",
    "ND": "农村道路",
    "SJ": "水工建筑",
    "CK": "采矿",
    "YT": "盐田",
    "LD": "公园绿地",
    "GF": "光伏",
    "1": "耕地",
    "01": "耕地",
    "2": "园地",
    "02": "园地",
    "3": "林地",
    "03": "林地",
    "4": "草地",
    "04": "草地",
    "SM": "水面",
    "KT": "坑塘",
    "SK": "水库",
    "HL": "河流",
    "GQ": "沟渠",
    "WL": "瓦砾",
    "TP": "推平",
    "QT": "其他",
    "GEF": "高尔夫",
    "ZQC": "足球场",
    "JC": "机场",
    "LT": "裸土地",
    "LY": "裸岩石砾地",
    "WH": "围海项目",
    "TH": "填海项目",
}


def humanize_table(
    columns: list[str],
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """把查询结果的表头与 TBLX 值映射为中文，并把面积列换算为亩。

    库内面积列（MJ、area、*_m2）都是平方米；这里统一改名为“面积(亩)”并按
    1 平方米 = 0.0015 亩 换算，保证表格 artifact 与给模型看的文本口径一致。
    未命中的列名/值原样保留。
    """
    labels: list[str] = []
    used: set[str] = set()
    for col in columns:
        if is_area_column(col):
            label = area_header(col)
        else:
            label = COLUMN_LABELS.get(col, col)
        if label in used:
            label = f"{label}[{col}]"
        used.add(label)
        labels.append(label)

    out_rows: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for col, label in zip(columns, labels):
            value = row.get(col)
            if col == "TBLX" and value is not None:
                value = TBLX_LABELS.get(str(value), value)
            elif is_area_column(col):
                value = m2_to_mu(value)
            new_row[label] = value
        out_rows.append(new_row)
    return labels, out_rows
