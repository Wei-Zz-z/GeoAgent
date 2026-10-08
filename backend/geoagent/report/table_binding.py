"""识别可按原有地区行安全回填的简单表格。"""

from __future__ import annotations

from typing import Any

from .template import PLACEHOLDER


def fixed_row_table_options(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """只开放单层表头、固定且唯一的行名；排名和合并表留待单独处理。"""
    slots = {slot["block_id"]: slot for slot in parsed["slots"] if slot["kind"] == "table_region"}
    options = []
    for block in parsed["blocks"]:
        if block["type"] != "table" or block["id"] not in slots:
            continue
        title = block.get("table_title") or block.get("caption") or ""
        headers = block["column_headers"]
        rows = block["rows"]
        if (block["header_rows"] != 1 or len(headers) < 2 or len(rows) < 3
                or "最多" in title or "前10" in title):
            continue
        labels = [header["label"].strip() for header in headers]
        if not all(labels) or len(labels) != len(set(labels)):
            continue
        data_rows = rows[1:]
        if any(len(row) != len(headers) or any(
            cell["grid_span"] != 1 or cell["vertical_merge"] is not None for cell in row
        ) for row in data_rows):
            continue
        row_keys = [row[0]["text"].strip() for row in data_rows]
        if (not all(row_keys) or len(row_keys) != len(set(row_keys))
                or any(PLACEHOLDER.search(key) for key in row_keys)):
            continue
        allowed = set(slots[block["id"]]["target_cell_ids"])
        options.append({
            "slot_id": slots[block["id"]]["id"],
            "block_id": block["id"],
            "title": title,
            "row_header": labels[0],
            "columns": labels[1:],
            "rows": [
                {
                    "key": key,
                    "cells": [
                        {"id": cell["id"], "column": labels[index], "editable": cell["id"] in allowed}
                        for index, cell in enumerate(row[1:], start=1)
                    ],
                }
                for key, row in zip(row_keys, data_rows)
            ],
        })
    return options
