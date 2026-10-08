"""按模板中的显式字段标记绑定标准问题的单行查询结果。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any


MARKER = re.compile(r"^\{\{([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)(\|mu)?\}\}$")


def bind_library_scalars(
    question: dict[str, Any], entry_id: str, result: dict[str, Any],
    slots: list[dict[str, Any]],
) -> dict[str, str]:
    """只绑定本问题内明确写出标准问题 ID、结果列和单位的数值位置。"""
    rows = result.get("rows", [])
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return {}
    source = rows[0]
    bindings: dict[str, str] = {}
    for slot in slots:
        if slot["id"] not in question["slot_ids"] or slot["kind"] != "placeholder":
            continue
        marker = MARKER.fullmatch(slot["text"])
        if marker is None or marker.group(1) != entry_id:
            continue
        column, unit = marker.group(2), marker.group(3)
        if column not in source or source[column] is None:
            continue
        try:
            value = Decimal(str(source[column]))
        except (InvalidOperation, ValueError):
            continue
        if not value.is_finite():
            continue
        if unit:
            if column != "area" and not column.endswith("_m2"):
                continue
            bindings[slot["id"]] = f"{value * Decimal('0.0015'):.2f}"
        else:
            bindings[slot["id"]] = format(value, "f")
    return bindings
