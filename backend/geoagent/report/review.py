"""模板问数覆盖检查：逐个核对来源、原子项与回填槽位。"""

from __future__ import annotations

from collections import Counter
from typing import Any


def audit_template_mapping(
    parsed: dict[str, Any],
    atomic_items: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """检查结构关系，不把规则检查结果冒充业务语义正确性。"""
    slot_ids = {item["id"] for item in parsed["slots"]}
    atomic_ids = {item["id"] for item in atomic_items}
    block_ids = {item["id"] for item in parsed["blocks"]}
    slot_references = Counter(slot for question in questions for slot in question["slot_ids"])
    atomic_references = Counter(item for question in questions for item in question["atomic_item_ids"])
    issues: list[dict[str, str]] = []

    for slot_id in sorted(slot_ids - slot_references.keys()):
        issues.append({"level": "error", "target": slot_id, "message": "待填位置没有对应问题"})
    for slot_id, count in slot_references.items():
        if slot_id not in slot_ids:
            issues.append({"level": "error", "target": slot_id, "message": "问题引用了不存在的待填位置"})
        elif count > 1:
            issues.append({"level": "warning", "target": slot_id, "message": "同一待填位置被多个问题引用"})
    for item_id in sorted(atomic_ids - atomic_references.keys()):
        issues.append({"level": "error", "target": item_id, "message": "原子查询项没有归入任何问题"})
    for question in questions:
        if question["source_block"] not in block_ids or not question["source_text"].strip():
            issues.append({"level": "error", "target": question["id"], "message": "问题缺少有效的模板原文来源"})
        if question["question_library_match"]["status"] != "candidate_found":
            issues.append({"level": "review", "target": question["id"], "message": "标准问题匹配仍需人工核对"})
    for anchor in parsed.get("chart_anchors", []):
        issues.append({
            "level": "review", "target": anchor["id"],
            "message": "图表位置已定位，绘图数据来源和类型仍需确认",
        })
    return {
        "method": "规则检查：逐一对照模板槽位、原子查询项、问题组和图表锚点",
        "slot_count": len(slot_ids),
        "covered_slot_count": len(slot_ids & slot_references.keys()),
        "atomic_item_count": len(atomic_ids),
        "covered_atomic_item_count": len(atomic_ids & atomic_references.keys()),
        "question_count": len(questions),
        "issues": issues,
    }
