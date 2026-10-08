"""自由问数答案的保守单值提取；模糊答案一律不猜填。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any
import json


SINGLE_VALUE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(个|亩|平方米|%)\s*[。！!]?\s*$")


def _raw_area_aggregate(sql: str, column: str) -> bool:
    """仅接受直接对 MJ 求平均或中位数；带换算或其他表达式时不推断单位。"""
    area = r'(?:[A-Za-z_][A-Za-z_0-9]*\.)?"MJ"'
    expression = (rf'(?:AVG\(\s*{area}\s*\)|'
                  rf'PERCENTILE_CONT\(\s*0\.5\s*\)\s+WITHIN\s+GROUP\s*'
                  rf'\(\s*ORDER\s+BY\s+{area}\s*\))')
    alias = re.escape(column)
    return bool(re.match(rf'^\s*SELECT\s+{expression}\s+AS\s+"?{alias}"?\s+FROM\s',
                         sql, re.IGNORECASE))


def query_scalar(messages: list[dict[str, Any]], expected_unit: str) -> dict[str, Any] | None:
    """从同一轮成功 SQL 工具的唯一单值表提取，不从模型解释文字猜数值。"""
    calls = {}
    for message in messages:
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            if function.get("name") == "run_sql":
                try:
                    args = json.loads(function.get("arguments", "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                calls[call["id"]] = args.get("sql")
    results = []
    for message in messages:
        sql = calls.get(message.get("tool_call_id"))
        if message.get("role") != "tool" or not sql or "结果已截断" in message.get("content", ""):
            continue
        for artifact in message.get("artifacts", []):
            if artifact.get("kind") != "table" or artifact.get("name") != "query_result":
                continue
            rows = artifact.get("data", {}).get("rows", [])
            if len(rows) != 1 or not isinstance(rows[0], dict) or len(rows[0]) != 1:
                continue
            column, value = next(iter(rows[0].items()))
            unit = ("平方米" if column.endswith("_m2") or "平方米" in column else
                    "亩" if column.endswith("_mu") or "亩" in column else
                    "个" if column in {"n", "图斑数量", "图斑数"} else None)
            if unit is None and _raw_area_aggregate(sql, column):
                unit = "平方米"
            if unit is None:
                continue
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError):
                continue
            if not number.is_finite():
                continue
            if unit == "个" and (number < 0 or number != number.to_integral_value()):
                continue
            if unit == "平方米" and expected_unit == "亩":
                number *= Decimal("0.0015")
            elif unit != expected_unit:
                continue
            results.append({"value": format(number, "f") if unit == "个" else f"{number:.2f}",
                            "sql": sql, "column": column, "unit": expected_unit,
                            "raw_result": rows[0], "tool_call_id": message["tool_call_id"]})
    return results[0] if len(results) == 1 else None


def unambiguous_scalar(
    answer: str,
    slot_ids: list[str],
    slots: list[dict[str, Any]],
    atomic_items: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """仅一处正文槽位、答案只有一个数字且单位一致时接受。"""
    if len(slot_ids) != 1:
        return None
    slot_id = slot_ids[0]
    slot = next((item for item in slots if item["id"] == slot_id), None)
    field = next((item for item in atomic_items if item["binding"]["slot_id"] == slot_id), None)
    if slot is None or slot["kind"] not in {"placeholder", "dynamic_number"} or field is None:
        return None
    match = SINGLE_VALUE.fullmatch(answer)
    if match is None:
        return None
    value, unit = match.groups()
    expected_unit = str(field.get("unit", "")).strip()
    if unit != expected_unit:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return slot_id, value
