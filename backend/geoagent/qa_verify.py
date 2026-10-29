"""确定性统计结果校验：把“模型说对了”变成“系统能证明对了”。

只校验可以程序化验证的事实，不重新解释业务口径：

- 空结果、负面积/负数量；
- 比例列必须在 0~100；
- 耕地流入/流出/净变化三行必须满足 净变化 = 流入 - 流出；
- 按类型汇总的分项合计必须等于工具给出的全库合计；
- 问题限定了区县时，结果里必须出现该区县；
- 面积列统一为亩，不能把平方米当亩展示。

校验失败不阻断回答，但会在正文里明确标注“未通过校验”，避免把可疑数字当结论。
"""

from __future__ import annotations

import re
from typing import Any

from .tools.result import Artifact, ToolResult


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _table_rows(result: ToolResult) -> list[tuple[Artifact, list[str], list[dict[str, Any]]]]:
    tables: list[tuple[Artifact, list[str], list[dict[str, Any]]]] = []
    for artifact in result.artifacts or []:
        if artifact.kind != "table":
            continue
        data = artifact.data or {}
        columns = [str(item) for item in (data.get("columns") or [])]
        rows = data.get("rows") or []
        tables.append((artifact, columns, rows))
    return tables


def _check_generic_numbers(tables: list[tuple[Artifact, list[str], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for artifact, columns, rows in tables:
        if not rows:
            checks.append(
                {"name": f"{artifact.name} 非空", "ok": False, "detail": "结果 0 行"}
            )
            continue
        for column in columns:
            values = [_as_number(row.get(column)) for row in rows]
            numeric = [value for value in values if value is not None]
            if not numeric:
                continue
            if "占比" in column or "%" in column:
                bad = [value for value in numeric if value < 0 or value > 100]
                if bad:
                    checks.append(
                        {
                            "name": f"{artifact.name}.{column} 比例范围",
                            "ok": False,
                            "detail": f"出现 0~100 之外的值：{bad[:3]}",
                        }
                    )
                else:
                    checks.append(
                        {"name": f"{artifact.name}.{column} 比例范围", "ok": True, "detail": "0~100"}
                    )
            if "面积" in column or "图斑数" in column or "数量" in column:
                if any(mark in column for mark in ("净变化", "差额", "增减")):
                    continue  # 净变化/差额允许为负
                bad = [value for value in numeric if value < 0]
                if bad:
                    checks.append(
                        {
                            "name": f"{artifact.name}.{column} 非负",
                            "ok": False,
                            "detail": f"出现负值：{bad[:3]}",
                        }
                    )
    return checks


def _check_net_change_table(tables: list[tuple[Artifact, list[str], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """表里同时有流出/流入/净变化列时，逐行校验 净变化 = 流入 - 流出。"""
    checks: list[dict[str, Any]] = []
    for artifact, columns, rows in tables:
        outflow_column = next((column for column in columns if "流出面积" in column), None)
        inflow_column = next((column for column in columns if "流入面积" in column), None)
        net_column = next((column for column in columns if "净变化面积" in column), None)
        if not (outflow_column and inflow_column and net_column):
            continue
        bad: list[str] = []
        for row in rows:
            outflow = _as_number(row.get(outflow_column))
            inflow = _as_number(row.get(inflow_column))
            net = _as_number(row.get(net_column))
            if None in (outflow, inflow, net):
                continue
            if abs((inflow - outflow) - net) > 0.05:
                bad.append(f"{row.get(columns[0])}: 净变化 {net:.2f}，重算 {(inflow - outflow):.2f}")
        checks.append(
            {
                "name": f"{artifact.name} 净变化=流入-流出",
                "ok": not bad,
                "detail": "；".join(bad[:3]) if bad else "逐行一致",
            }
        )
    return checks


def _check_farmland_flow(result: ToolResult) -> list[dict[str, Any]]:
    """耕地流出/流入/净变化必须自洽（亩，允许两位小数舍入误差）。"""
    for artifact, columns, rows in _table_rows(result):
        if artifact.name != "farmland_metrics":
            continue
        by_label: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = str(row.get("指标") or "")
            by_label[label] = row
        outgoing = next((row for label, row in by_label.items() if "流出" in label), None)
        incoming = next((row for label, row in by_label.items() if "流入" in label), None)
        net = next((row for label, row in by_label.items() if "净变化" in label), None)
        if not (outgoing and incoming and net):
            return [{"name": "耕地流向三行齐全", "ok": False, "detail": "缺少流出/流入/净变化行"}]
        area_column = next((column for column in columns if "面积" in column), None)
        if not area_column:
            return [{"name": "耕地流向面积列", "ok": False, "detail": "缺少面积列"}]
        out_value = _as_number(outgoing.get(area_column))
        in_value = _as_number(incoming.get(area_column))
        net_value = _as_number(net.get(area_column))
        if None in (out_value, in_value, net_value):
            return [{"name": "耕地流向数值", "ok": False, "detail": "面积不是数值"}]
        expected = in_value - out_value
        ok = abs(expected - net_value) <= 0.05
        return [
            {
                "name": "耕地净变化=流入-流出",
                "ok": ok,
                "detail": f"净变化 {net_value:.2f} 亩，重算 {expected:.2f} 亩",
            }
        ]
    return []


def _check_by_type_total(result: ToolResult) -> list[dict[str, Any]]:
    """按类型汇总：分项图斑数/面积之和应等于正文里的全库合计。"""
    matched = re.search(
        r"全库合计[:：]\s*共\s*([\d,]+)\s*个图斑、总面积\s*([\d,.]+)\s*亩",
        str(result.content or ""),
    )
    if not matched:
        return []
    expected_n = float(matched.group(1).replace(",", ""))
    expected_area = float(matched.group(2).replace(",", ""))
    for artifact, columns, rows in _table_rows(result):
        if artifact.name != "by_type_summary":
            continue
        count_column = next((column for column in columns if "图斑数" in column), None)
        area_column = next((column for column in columns if "面积" in column), None)
        if not count_column or not area_column:
            return []
        total_n = sum(_as_number(row.get(count_column)) or 0.0 for row in rows)
        total_area = sum(_as_number(row.get(area_column)) or 0.0 for row in rows)
        ok_n = abs(total_n - expected_n) < 0.5
        ok_area = abs(total_area - expected_area) <= max(0.1, expected_area * 0.001)
        return [
            {
                "name": "分项图斑数合计=全库合计",
                "ok": ok_n,
                "detail": f"分项 {total_n:.0f}，全库 {expected_n:.0f}",
            },
            {
                "name": "分项面积合计=全库合计",
                "ok": ok_area,
                "detail": f"分项 {total_area:.2f} 亩，全库 {expected_area:.2f} 亩",
            },
        ]
    return []


def _check_county(result: ToolResult, county: str) -> list[dict[str, Any]]:
    if not county:
        return []
    tables = _table_rows(result)
    values: list[str] = []
    for _, _, rows in tables:
        for row in rows:
            values.extend(str(value) for value in row.values() if value is not None)
    text = str(result.content or "")
    ok = county in text or any(county in value for value in values)
    return [
        {
            "name": "区县筛选命中",
            "ok": ok,
            "detail": f"结果中{'出现' if ok else '未出现'}“{county}”",
        }
    ]


def verify_matched_result(
    entry: dict[str, Any],
    result: ToolResult,
    *,
    county: str = "",
) -> dict[str, Any]:
    """校验一次确定性执行结果；返回 {ok, checks, warnings}。"""
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    tables = _table_rows(result)
    if not tables:
        warnings.append("本次执行没有表格产物，无法做数量/比例校验")
    checks.extend(_check_generic_numbers(tables))
    checks.extend(_check_net_change_table(tables))

    tool = str(entry.get("tool") or "")
    if tool == "farmland_flow_summary":
        checks.extend(_check_farmland_flow(result))
    if tool == "summarize_by_type":
        checks.extend(_check_by_type_total(result))
    if county:
        checks.extend(_check_county(result, county))

    body = str(result.content or "")
    if "平方米" in body and "亩" not in body:
        warnings.append("正文出现平方米但没有亩，可能单位不一致")

    failed = [check for check in checks if not check.get("ok")]
    return {
        "ok": not failed,
        "checks": checks,
        "warnings": warnings,
        "failed": failed,
    }


def verification_text(verification: dict[str, Any]) -> str:
    """把校验结果压成一行中文，供正文/事件复用。"""
    checks = verification.get("checks") or []
    failed = verification.get("failed") or []
    if failed:
        details = "；".join(
            f"{item.get('name')}（{item.get('detail')}）" for item in failed[:3]
        )
        return f"结果校验未通过：{details}。请以明细/原始查询为准。"
    passed = [str(item.get("name")) for item in checks if item.get("ok")]
    if passed:
        return "结果校验通过：" + "；".join(passed[:4]) + "。"
    return ""
