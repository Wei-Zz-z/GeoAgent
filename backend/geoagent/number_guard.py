"""回答正文的数字溯源（number guard）。

用途：自由 SQL 路径里，模型会基于工具结果写自然语言回答；工具结果可信，但正文
数字可能被模型改写、心算错、混单位或直接编造。本模块把正文里出现的每个数字与
本轮工具结果（含 table artifact）对齐：

- 直接命中工具结果中的数值；
- 允许平方米 ↔ 亩换算；
- 允许由两个工具数值计算出的百分比（回答必须带 %）；
- 允许引用用户问题里的参数（如“小于 100 平方米”的 100）和期次年份。

未命中的数字不会被悄悄当成事实：调用方先尝试单位修正，再用工具证据重写一次；
仍无法溯源的回答会被阻断，只保留查询结果表格。``number_guard`` 的
``corrections / rewrite_attempted / blocked`` 会挂到 message 事件上。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class NumberToken:
    raw: str
    value: float
    unit: str = ""
    percent: bool = False

    def label(self) -> str:
        return f"{self.raw}{self.unit}"


_NUMBER_RE = re.compile(
    r"(?<![0-9A-Za-z._])(-?\d[\d,]*(?:\.\d+)?)\s*(万|亿|%|％|平方米|平米|亩|公顷|个|块|条|种|行|年)?"
)
_M2_UNITS = {"平方米", "平米"}
_MU_UNITS = {"亩"}
_AREA_UNITS = _M2_UNITS | _MU_UNITS | {"公顷"}


def extract_numbers(text: str) -> list[NumberToken]:
    """从一段中文文本中抽取数字、单位和百分号。"""
    tokens: list[NumberToken] = []
    for match in _NUMBER_RE.finditer(str(text or "")):
        raw_number, unit = match.group(1), match.group(2) or ""
        try:
            value = float(raw_number.replace(",", ""))
        except ValueError:
            continue
        multiplier = 10000.0 if unit == "万" else 100000000.0 if unit == "亿" else 1.0
        tokens.append(
            NumberToken(
                raw=raw_number,
                value=value * multiplier,
                unit=unit,
                percent=unit in {"%", "％"},
            )
        )
    return tokens


def _walk_numbers(value: Any, output: list[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        output.append(float(value))
        return
    if isinstance(value, str):
        output.extend(token.value for token in extract_numbers(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_numbers(item, output)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _walk_numbers(item, output)


def _tool_evidence(tool_messages: Iterable[dict[str, Any]]) -> tuple[list[float], list[float]]:
    """返回（原始数值池，百分比池）。百分比池包含由任意两数算出的占比。"""
    values: list[float] = []
    for message in tool_messages:
        if message.get("role") != "tool":
            continue
        _walk_numbers(message.get("content"), values)
        _walk_numbers(message.get("artifacts"), values)
    # 单位换算：工具结果可能是平方米，正文展示为亩；两个方向都纳入证据。
    expanded = list(values)
    for value in values:
        expanded.append(value * 0.0015)
        expanded.append(value / 0.0015)
    percentages: list[float] = []
    for value in values:
        if 0.0 <= value <= 100.0:
            percentages.append(value)
    # 允许模型由分子/分母计算的百分比（只接受 0~100 的结果）。
    positive = [value for value in values if value > 0]
    for index, numerator in enumerate(positive[:120]):
        for denominator in positive[:120]:
            ratio = numerator / denominator * 100.0
            if 0.0 <= ratio <= 100.0:
                percentages.append(ratio)
    return expanded, percentages


def _near(value: float, target: float) -> bool:
    tolerance = max(0.01, abs(target) * 0.005)
    return math.isclose(value, target, rel_tol=0.0, abs_tol=tolerance)


def verify_numbers(
    answer: str,
    tool_messages: Iterable[dict[str, Any]],
    *,
    question: str = "",
) -> dict[str, Any]:
    """校验回答正文中的数字是否有本轮工具结果/用户参数作为出处。"""
    tokens = extract_numbers(answer)
    if not tokens:
        return {"ok": True, "checked": 0, "unverified": [], "evidence_count": 0}

    evidence_values, evidence_percentages = _tool_evidence(tool_messages)
    allowed_values: list[float] = []
    for token in extract_numbers(question):
        allowed_values.extend([token.value, token.value * 0.0015, token.value / 0.0015])
    unverified: list[NumberToken] = []
    checked = 0

    for token in tokens:
        # 期次年份、用户问题里的阈值/数字允许作为“引用”出现。
        if 1900 <= token.value <= 2100 and (token.unit == "年" or not token.unit):
            continue
        if any(_near(token.value, value) for value in allowed_values):
            continue
        # 像 01 / 0101 / 20 这种编码不按数值指标校验，避免把 TBLX 编码当成错误。
        raw_digits = token.raw.replace(",", "")
        if not token.unit and raw_digits.startswith("0") and "." not in raw_digits:
            continue
        checked += 1
        candidates: list[float] = []
        if token.percent:
            candidates = evidence_percentages
        elif token.unit in _M2_UNITS:
            candidates = [token.value] + [value for value in evidence_values]
            # 直接比较平方米；evidence_values 已含 m²、亩两种量纲。
        elif token.unit in _MU_UNITS:
            candidates = evidence_values
        elif token.unit in _AREA_UNITS:
            candidates = evidence_values
        else:
            candidates = evidence_values
        if not any(_near(token.value, candidate) for candidate in candidates):
            unverified.append(token)

    return {
        "ok": not unverified,
        "checked": checked,
        "unverified": [
            {"value": token.value, "raw": token.raw, "unit": token.unit}
            for token in unverified
        ],
        "evidence_count": len(evidence_values),
    }


def warning_text(result: dict[str, Any], limit: int = 5) -> str:
    """把未溯源数字压成一句用户可见的警示。"""
    unverified = result.get("unverified") or []
    if not unverified:
        return ""
    labels = []
    for item in unverified[:limit]:
        unit = str(item.get("unit") or "")
        labels.append(f"{item.get('raw')}{unit}")
    more = f"等 {len(unverified)} 个" if len(unverified) > limit else ""
    return (
        "⚠ 数字溯源：以下数字未能从本轮查询结果中找到出处"
        "（可能是心算误差、单位换算错误或编造）："
        + "、".join(labels)
        + more
        + "。请以上方表格/原始查询结果为准。"
    )


# ---------------------------------------------------------------- claim-level


@dataclass(frozen=True)
class EvidenceValue:
    value: float
    unit: str = ""


def _walk_evidence(value: Any, unit_hint: str, output: list[EvidenceValue]) -> None:
    """从工具内容/artifact 中抽取带单位的证据值。"""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        output.append(EvidenceValue(float(value), unit_hint))
        return
    if isinstance(value, str):
        output.extend(
            EvidenceValue(token.value, token.unit) for token in extract_numbers(value)
        )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            hint = unit_hint
            if "亩" in key_text:
                hint = "亩"
            elif "平方米" in key_text or "平米" in key_text:
                hint = "平方米"
            elif "占比" in key_text or "比例" in key_text or "%" in key_text:
                hint = "%"
            elif "个" in key_text or "数量" in key_text or "条" in key_text:
                hint = "个"
            _walk_evidence(item, hint, output)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _walk_evidence(item, unit_hint, output)


def _evidence_entries(tool_messages: Iterable[dict[str, Any]]) -> list[EvidenceValue]:
    entries: list[EvidenceValue] = []
    for message in tool_messages:
        if message.get("role") != "tool":
            continue
        _walk_evidence(message.get("content"), "", entries)
        _walk_evidence(message.get("artifacts"), "", entries)
    return entries


def _area_candidates(token: NumberToken, entries: list[EvidenceValue]) -> list[float]:
    """按正文单位给出可比数值：平方米 ↔ 亩，兼容无单位工具值。"""
    values: list[float] = []
    for entry in entries:
        if entry.unit in _M2_UNITS or entry.unit == "":
            if token.unit in _MU_UNITS:
                values.append(entry.value * 0.0015)
            else:
                values.append(entry.value)
        if entry.unit in _MU_UNITS or entry.unit == "":
            if token.unit in _M2_UNITS:
                values.append(entry.value / 0.0015)
            else:
                values.append(entry.value)
    return values


def _unit_correction(
    token: NumberToken, entries: list[EvidenceValue]
) -> Optional[dict[str, Any]]:
    """识别“平方米写成亩 / 乘 1.5”这类可机械修正的单位错误。"""
    if token.unit not in _AREA_UNITS:
        return None
    for entry in entries:
        if entry.unit not in _AREA_UNITS and entry.unit != "":
            continue
        if token.unit in _MU_UNITS and entry.unit in _M2_UNITS:
            # 典型：436612.85 平方米写成 654919.28 亩（乘了 1.5）。
            if _near(token.value, entry.value * 1.5):
                return {
                    "raw": token.raw,
                    "unit": token.unit,
                    "value": token.value,
                    "corrected": entry.value * 0.0015,
                    "reason": "平方米误按 ×1.5 写成亩",
                }
            # 把平方米数值直接写成亩。
            if _near(token.value, entry.value):
                return {
                    "raw": token.raw,
                    "unit": token.unit,
                    "value": token.value,
                    "corrected": entry.value * 0.0015,
                    "reason": "平方米数值误标为亩",
                }
        if token.unit in _M2_UNITS and entry.unit in _MU_UNITS:
            if _near(token.value, entry.value):
                return {
                    "raw": token.raw,
                    "unit": token.unit,
                    "value": token.value,
                    "corrected": entry.value / 0.0015,
                    "reason": "亩数值误标为平方米",
                }
    return None


def _format_corrected(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def apply_corrections(answer: str, corrections: Iterable[dict[str, Any]]) -> str:
    """按 claim-level 校验结果替换可机械修正的数字。"""
    text = str(answer or "")
    for item in corrections:
        raw = str(item.get("raw") or "")
        unit = str(item.get("unit") or "")
        value = float(item.get("corrected") or 0.0)
        if not raw:
            continue
        replacement = _format_corrected(value) + unit
        text = re.sub(
            rf"{re.escape(raw)}\s*{re.escape(unit)}",
            replacement,
            text,
            count=1,
        )
    return text


def analyze_claims(
    answer: str,
    tool_messages: Iterable[dict[str, Any]],
    *,
    question: str = "",
) -> dict[str, Any]:
    """claim-level 校验：逐数字检查出处，并给出可机械修正的单位错误。"""
    tokens = extract_numbers(answer)
    entries = _evidence_entries(tool_messages)
    evidence_values = [entry.value for entry in entries]
    percentages = [value for value in evidence_values if 0.0 <= value <= 100.0]
    positive = [value for value in evidence_values if value > 0]
    for numerator in positive[:120]:
        for denominator in positive[:120]:
            ratio = numerator / denominator * 100.0
            if 0.0 <= ratio <= 100.0:
                percentages.append(ratio)
    allowed_values: list[float] = []
    for token in extract_numbers(question):
        allowed_values.extend([token.value, token.value * 0.0015, token.value / 0.0015])

    unverified: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    checked = 0
    for token in tokens:
        if 1900 <= token.value <= 2100 and (token.unit == "年" or not token.unit):
            continue
        if any(_near(token.value, value) for value in allowed_values):
            continue
        raw_digits = token.raw.replace(",", "")
        if not token.unit and raw_digits.startswith("0") and "." not in raw_digits:
            continue
        checked += 1
        if token.percent:
            candidates = percentages
        elif token.unit in _AREA_UNITS:
            candidates = _area_candidates(token, entries)
        elif token.unit in {"个", "块", "条", "种", "行"}:
            candidates = [
                entry.value
                for entry in entries
                if entry.unit in {"", token.unit}
            ] or evidence_values
        else:
            candidates = evidence_values
        if any(_near(token.value, candidate) for candidate in candidates):
            continue
        correction = _unit_correction(token, entries)
        if correction:
            corrections.append(correction)
            continue
        unverified.append(
            {
                "value": token.value,
                "raw": token.raw,
                "unit": token.unit,
                "context": answer[max(0, answer.find(token.raw) - 20): answer.find(token.raw) + len(token.raw) + 20],
            }
        )
    return {
        "ok": not unverified,
        "checked": checked,
        "unverified": unverified,
        "corrections": corrections,
        "evidence_count": len(entries),
    }


def evidence_digest(tool_messages: Iterable[dict[str, Any]], limit: int = 3500) -> str:
    """给重写提示词的紧凑证据摘要（只保留工具 content，避免暴露内部结构）。"""
    parts: list[str] = []
    for message in tool_messages:
        if message.get("role") != "tool":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            parts.append(content)
    text = "\n\n".join(parts)
    return text[:limit]
