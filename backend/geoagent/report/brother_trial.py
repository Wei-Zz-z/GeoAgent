"""师兄快报挖空原件的受控查询结果回填。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .template_fill import refill_docx


TEMPLATE_SHA256 = "017e4b37c701232947091a4b0fede73ce950eb224f299cd5f3cf2b54d2f57b1c"


def is_brother_template(parsed: dict[str, Any]) -> bool:
    """仅认已检查的挖空原件，避免将相似版面错用固定口径。"""
    return (
        parsed.get("sha256") == TEMPLATE_SHA256
        and len(parsed.get("blocks", [])) == 30
        and len(parsed.get("slots", [])) == 20
        and [block["id"] for block in parsed["blocks"] if block["type"] == "table"]
        == ["b10", "b12", "b16"]
    )


def _mu(area: Any) -> str:
    return f"{float(area) * 0.0015:.2f}"


def _count(value: Any) -> str:
    return str(int(value))


def brother_answers(
    parsed: dict[str, Any], stats: dict[str, Any]
) -> tuple[dict[str, str], dict[str, list[list[str]]]]:
    """把现有确定性快报统计量绑定到这份原件的明确槽位。"""
    original = stats["orig_crop"]
    current = stats["cur_crop"]
    to_construction = stats["crop2const"]
    restored = stats["restore2crop"]
    construction = stats["cur_const"]
    scalar = {
        "b6:s0": _count(stats["total_n"]),
        "b6:s1": _mu(stats["total_area"]),
        "b8:s0": _count(original["n"]),
        "b8:s1": _mu(original["area"]),
        "b8:s2": _count(current["n"]),
        "b8:s3": _mu(current["area"]),
        "b8:s4": _count(stats["crop_net_n"]),
        "b8:s5": _mu(stats["crop_net_area"]),
        "b9:s0": _count(to_construction["n"]),
        "b9:s1": _mu(to_construction["area"]),
        "b9:s2": _count(restored["n"]),
        "b9:s3": _mu(restored["area"]),
        "b14:s0": _count(construction["n"]),
        "b14:s1": _mu(construction["area"]),
        "b14:s2": _count(to_construction["n"]),
        "b14:s3": _mu(to_construction["area"]),
        "b14:s4": f"{float(stats['crop2const_pct']):.2f}",
    }
    top_net = stats["top_net_counties"]
    top_const = stats["top_const_counties"]
    tables = {
        "b10:table": [
            ["变化前为耕地（原）", _count(original["n"]), _mu(original["area"])],
            ["变化后为耕地（现）", _count(current["n"]), _mu(current["area"])],
            ["耕地净变化（现−原）", _count(stats["crop_net_n"]), _mu(stats["crop_net_area"])],
            ["其中：原耕地流向建设用地", _count(to_construction["n"]), _mu(to_construction["area"])],
            ["其中：恢复性地类流入耕地", _count(restored["n"]), _mu(restored["area"])],
        ],
        "b12:table": [
            [str(row["xmc"]), _mu(row["outflow"]), _mu(row["inflow"]), _mu(row["net"])]
            for row in top_net
        ],
        "b16:table": [
            [str(row["xmc"]), _count(row["n"]), _mu(row["area"])]
            for row in top_const
        ],
    }
    available = {slot["id"] for slot in parsed["slots"]}
    if set(scalar) | set(tables) != available:
        raise ValueError("师兄模板的待填位置发生变化，请重新核对，不自动猜填。")
    return scalar, tables


def fill_brother_report(
    template_path: Path,
    parsed: dict[str, Any],
    stats: dict[str, Any],
    output_path: Path,
    *,
    query_started_at: datetime,
) -> dict[str, Any]:
    """按原位置回填；缺少违法认定数据的段落继续标为待核实。"""
    if not is_brother_template(parsed):
        raise ValueError("仅支持已核验的师兄快报挖空原件。")
    scalars, tables = brother_answers(parsed, stats)
    trend = (
        "呈净减少态势。" if stats["crop_net_area"] < 0 else
        "呈净增加态势。" if stats["crop_net_area"] > 0 else
        "总体持平。"
    )
    refill_docx(
        template_path, parsed, output_path,
        scalar_answers=scalars,
        table_answers=tables,
        test_notice=(
            "【核验版】本报告使用2026年第一期图斑库固定统计口径；"
            "TBLX分类映射为临时规则，疑似违法占地缺少管理信息，均待业务核实。"
        ),
        text_replacements={
            "[[SYSTEM_TIME]]": query_started_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            "平方米": "亩",
            "㎡": "亩",
            "本版未做单位换算": "本版已将面积由平方米换算为亩",
            "呈净减少态势。": trend,
        },
    )
    return {
        "filled_scalars": len(scalars),
        "filled_tables": len(tables),
        "charts": 0,
        "pending_items": ["疑似违法占地及相关管理信息", "TBLX三大类正式映射口径"],
    }
