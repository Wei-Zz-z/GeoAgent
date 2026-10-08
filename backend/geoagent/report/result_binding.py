"""普通模板按指标、结果列及单位绑定；不按模板文件名或哈希选择答案。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .template import decompose_atomic_items
from ..tools.labels import TBLX_LABELS


def result_column(label: str, columns: list[str]) -> str | None:
    """仅识别有明确含义的结果列；有多个候选时不猜测。"""
    aliases = {
        "inflow_m2": ("流入",), "outflow_m2": ("流出",),
        "net_m2": ("净变化",), "n": ("数量", "图斑数",),
        "area": ("面积",), "area_m2": ("面积",),
        "region": ("行政区划", "县（市、区）", "县级行政区",),
        "TBLX": ("图斑类型",), "source_type": ("来源地类",),
        "share_pct": ("占比",),
    }
    specific = [column for column in ("inflow_m2", "outflow_m2", "net_m2")
                if column in columns and any(word in label for word in aliases[column])]
    if specific:
        return specific[0] if len(specific) == 1 else None
    if any(word in label for word in ("流入", "流出", "净变化")):
        return None
    if label.endswith("面积"):
        areas = [column for column in columns if column in {"area", "area_m2"}]
        return areas[0] if len(areas) == 1 else None
    candidates = [column for column in columns
                  if any(word in label for word in aliases.get(column, ())) ]
    return candidates[0] if len(candidates) == 1 else None


def format_result(value: Any, column: str, unit: str | None) -> str | None:
    if value is None:
        return None
    if column in {"region", "source_type", "TBLX"}:
        return str(TBLX_LABELS.get(str(value), value)) if column == "TBLX" else str(value)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    if column == "n":
        return str(int(number)) if unit == "个" and number == number.to_integral_value() and number >= 0 else None
    if column == "share_pct":
        return f"{number:.2f}" if unit == "%" else None
    if column == "area" or column.endswith("_m2"):
        factors = {"平方米": "1", "亩": "0.0015", "万亩": "0.00000015", "公顷": "0.0001"}
        return f"{number * Decimal(factors[unit]):.2f}" if unit in factors else None
    return None


def bind_result(question: dict[str, Any], parsed: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """绑定当前问题所属位置，保留来源列；不按返回顺序配对数值。"""
    rows = result.get("rows", [])
    answer: dict[str, Any] = {"scalars": {}, "tables": {}, "sources": {}}
    if result.get("truncated") or not rows or not all(isinstance(row, dict) for row in rows):
        return answer
    columns = list(rows[0])
    for item in decompose_atomic_items(parsed):
        slot_id = item["binding"]["slot_id"]
        if slot_id not in question["slot_ids"] or item["kind"] != "scalar" or len(rows) != 1:
            continue
        column = result_column(item["metric"], columns)
        if column is None:
            continue
        value = format_result(rows[0].get(column), column, item.get("unit"))
        if value is not None:
            answer["scalars"][slot_id] = value
            answer["sources"][slot_id] = {"column": column, "unit": item.get("unit"), "row": 0}
    for slot in parsed["slots"]:
        if slot["id"] not in question["slot_ids"] or slot["kind"] != "table_region":
            continue
        block = next(block for block in parsed["blocks"] if block["id"] == slot["block_id"])
        # 固定地区或指标行不能用另一批结果行顺序覆盖。
        if any(cell["text"].strip() for row in block["rows"][block["header_rows"]:]
               for cell in row):
            continue
        mapped = [result_column(header["label"], columns) for header in block["column_headers"]]
        if not mapped or None in mapped or len(set(mapped)) != len(mapped):
            continue
        limit = re.search(r"前\s*(\d+)\s*位", block.get("table_title", ""))
        selected_rows = rows[:int(limit.group(1))] if limit else rows
        formatted = []
        for row in selected_rows:
            values = [format_result(row.get(column), column,
                      "个" if column == "n" else "%" if column == "share_pct" else block.get("unit"))
                      for column in mapped]
            if None in values:
                break
            formatted.append(values)
        else:
            answer["tables"][slot["id"]] = formatted
            answer["sources"][slot["id"]] = {"columns": mapped, "unit": block.get("unit")}
    return answer


def bind_charts(parsed: dict[str, Any], tables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """图题和已绑定表格具有同一明确分组维度时共享数据，否则保持待核实。"""
    charts = {}
    for anchor in parsed.get("chart_anchors", []):
        caption = anchor["caption"]
        dimensions = ("图斑类型", "各县", "来源")
        candidates = []
        for slot_id, table in tables.items():
            block = next(block for block in parsed["blocks"] if block["id"] == slot_id.split(":")[0])
            title = block.get("table_title", "")
            if any(word in caption and word in title for word in dimensions):
                candidates.append(table)
        if len(candidates) != 1:
            continue
        table = candidates[0]
        columns = table["source"]["columns"]
        label_columns = [i for i, column in enumerate(columns) if column in {"TBLX", "region", "source_type"}]
        area_columns = [i for i, column in enumerate(columns) if column in {"area", "area_m2"}]
        if len(label_columns) != 1 or len(area_columns) != 1:
            continue
        rows = table["rows"]
        values = [float(row[area_columns[0]]) for row in rows]
        kind = anchor.get("chart_type_hint", "bar")
        if kind == "pie" and (any(value < 0 for value in values) or sum(values) <= 0):
            continue
        labels = [row[label_columns[0]] for row in rows]
        grouped = []
        if kind == "pie" and len(rows) > 6:
            # 表格仍保留全部类型；图中仅合并长尾，避免小扇区标签重叠。
            ranked = sorted(zip(labels, values), key=lambda pair: pair[1], reverse=True)
            grouped = [label for label, _ in ranked[5:]]
            labels = [label for label, _ in ranked[:5]] + ["其余类型（合计）"]
            values = [value for _, value in ranked[:5]] + [sum(value for _, value in ranked[5:])]
        charts[anchor["id"]] = {
            "chart_type": kind, "labels": labels,
            "values": values, "source_question_id": table["source_question_id"],
            "source_table": table["slot_id"],
            "grouped_categories": grouped,
        }
    return charts
