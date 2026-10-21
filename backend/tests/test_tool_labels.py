"""前端工具卡片中文名覆盖检查：新增工具忘了登记中文名时直接失败。"""

from __future__ import annotations

import re
from pathlib import Path

import geoagent.agents.elder_care  # noqa: F401  确保场景工具已注册
import geoagent.agents.sql  # noqa: F401
from geoagent.tools import all_tools

LABELS_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "toolLabels.js"


def _labels() -> dict[str, str]:
    text = LABELS_PATH.read_text(encoding="utf-8")
    return {
        name: label
        for name, label in re.findall(
            r"^\s*([a-z_][a-z0-9_]*):\s*'([^']*)'", text, flags=re.M
        )
    }


def test_every_registered_tool_has_chinese_label():
    labels = _labels()
    missing = sorted(tool.name for tool in all_tools() if tool.name not in labels)
    assert not missing, (
        "以下工具缺中文名，请在 frontend/src/toolLabels.js 的 TOOL_LABELS 中登记："
        f"{missing}"
    )


def test_tool_labels_are_chinese():
    for name, label in _labels().items():
        assert any("\u4e00" <= ch <= "\u9fff" for ch in label), (
            f"{name} 的卡片名不含汉字：{label}"
        )


ARTIFACT_LABELS_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "artifactLabels.js"


def _object_block(text: str, marker: str) -> str:
    """截取 JS 里 const XXX = { ... } 的键值块（不引入 JS 解析器）。"""
    assert marker in text, f"artifactLabels.js 缺少 {marker}"
    return text.split(marker, 1)[1].split("}", 1)[0]


def _artifact_labels() -> dict[str, str]:
    block = _object_block(
        ARTIFACT_LABELS_PATH.read_text(encoding="utf-8"), "const ARTIFACT_LABELS = {"
    )
    return {
        name: label
        for name, label in re.findall(
            r"^\s*([a-z_][a-z0-9_]*):\s*'([^']*)'", block, flags=re.M
        )
    }


def test_artifact_labels_are_chinese():
    labels = _artifact_labels()
    assert labels, "artifactLabels.js 里没有登记任何卡片中文名"
    for name, label in labels.items():
        assert any("\u4e00" <= ch <= "\u9fff" for ch in label), (
            f"卡片 {name} 的中文名不含汉字：{label}"
        )


def test_artifact_kind_fallback_is_chinese():
    """未登记的英文标识符会走类型兜底，兜底文案同样必须是中文。"""
    block = _object_block(
        ARTIFACT_LABELS_PATH.read_text(encoding="utf-8"), "const KIND_FALLBACK = {"
    )
    values = re.findall(r":\s*'([^']*)'", block)
    assert values, "artifactLabels.js 缺少 KIND_FALLBACK"
    for value in values:
        assert any("\u4e00" <= ch <= "\u9fff" for ch in value), f"兜底文案不是中文：{value}"
