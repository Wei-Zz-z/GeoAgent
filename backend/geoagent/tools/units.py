"""面积单位换算：库内面积一律是平方米，对外展示与回答统一用亩。

数据事实：主表 ``MJ`` 与统计 SQL 里的 ``area`` / ``area_m2`` / ``*_m2`` 列都是平方米；
业务口径：对话中的面积统一按亩展示（1 亩 = 666.6667 平方米，即 1 平方米 = 0.0015 亩，
保留两位小数）。

本模块只服务"对话展示层"：查询结果的表格 artifact、给模型看的工具文本、确定性统计工具的
表格。报告/模板链路各自在展示端换算（``report/briefing.py``、``server/template_routes.py``），
不经过这里，因此不会重复换算。
"""

from __future__ import annotations

import re
from typing import Any

# 1 平方米 = 0.0015 亩
MU_PER_M2 = 0.0015
MU_DIGITS = 2
MU_UNIT = "亩"

# 面积列名 → 中文表头（不含单位）
_AREA_LABELS: dict[str, str] = {
    "mj": "面积",
    "area": "面积",
    "area_m2": "面积",
    "total_area": "总面积",
    "total_area_m2": "总面积",
    "outflow_m2": "流出面积",
    "inflow_m2": "流入面积",
    "net_m2": "净变化面积",
    "jsyd_area_m2": "建设用地面积",
    "mean_area_m2": "平均面积",
    "avg_mj": "平均面积",
    "farmland_inflow_m2": "耕地流入面积",
    "farmland_outflow_m2": "耕地流出面积",
    "farmland_net_m2": "耕地净变化面积",
}

# 已经是亩的列不再换算
_ALREADY_MU = {"area_mu"}

# 表头里出现这些字样时，说明该列是平方米（确定性统计工具的表格用中文表头）
_M2_MARKS = ("平方米", "㎡")


def is_area_column(name: Any) -> bool:
    """判断列名是否为平方米口径的面积列（已是亩的列返回 False）。"""
    key = str(name or "").strip().lower()
    if not key or key in _ALREADY_MU:
        return False
    if key in _AREA_LABELS:
        return True
    return key.endswith("m2")


def area_header(name: Any) -> str:
    """面积列的中文表头（带亩单位）。"""
    key = str(name or "").strip().lower()
    base = _AREA_LABELS.get(key)
    if not base:
        stripped = re.sub(r"_?m2$", "", key).strip("_") or "面积"
        base = "面积" if stripped == "mj" else stripped
    return f"{base}({MU_UNIT})"


def m2_to_mu(value: Any) -> Any:
    """平方米 → 亩；None 与非数值原样返回，结果保留两位小数。"""
    if value is None or isinstance(value, bool):
        return value
    try:
        return round(float(value) * MU_PER_M2, MU_DIGITS)
    except (TypeError, ValueError):
        return value


def convert_area_columns(
    columns: list[str],
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """把平方米面积列换算为亩并改中文表头（其它列原样保留）。"""
    labels: list[str] = []
    used: set[str] = set()
    for col in columns:
        label = area_header(col) if is_area_column(col) else col
        if label in used:
            label = f"{label}[{col}]"
        used.add(label)
        labels.append(label)

    out_rows: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for col, label in zip(columns, labels):
            value = row.get(col)
            new_row[label] = m2_to_mu(value) if is_area_column(col) else value
        out_rows.append(new_row)
    return labels, out_rows


def convert_mu_headers(
    columns: list[str],
    rows: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """把表头里写着“平方米/㎡”的列改名为亩并换算数值。

    确定性统计工具（tools/stat.py）直接用中文表头建表，走这个入口即可整体换算。
    """
    mapping: dict[str, str] = {}
    for col in columns:
        text = str(col)
        if any(mark in text for mark in _M2_MARKS):
            renamed = text
            for mark in _M2_MARKS:
                renamed = renamed.replace(mark, MU_UNIT)
            mapping[col] = renamed
    if not mapping:
        return list(columns), [dict(row) for row in rows]

    new_columns = [mapping.get(col, col) for col in columns]
    new_rows: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for key, value in row.items():
            if key in mapping:
                new_row[mapping[key]] = m2_to_mu(value)
            else:
                new_row[key] = value
        new_rows.append(new_row)
    return new_columns, new_rows


def normalize_unit(unit: Any) -> str:
    """把条目里的旧单位写法统一为亩，避免正文单位与表格不一致。"""
    text = str(unit or "").strip()
    if any(mark in text for mark in _M2_MARKS) or text.lower() in {"m2", "square_meter"}:
        return MU_UNIT
    return text