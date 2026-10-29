"""LLM judge：只在“候选/澄清”灰度区做一次口径复核。

作用不是替代匹配，而是给低置信场景加一道安全阀：

- 输入 top-3 候选 + 已识别槽位；
- 模型只能从候选 id 中选一个，或回答 ``none``；
- 只在 ``decision`` 为 candidates/clarify 时调用，正常 execute 不增加模型调用；
- 解析失败、超时、未配置模型时返回 None，整条链路继续按原决策工作。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

JUDGE_SYSTEM_PROMPT = (
    "你是土地变化统计问答的口径复核器。你只能从给定的候选标准问题中选择，"
    "不能新增候选、不能编造口径。输出必须是 JSON，不要 Markdown。"
)

JUDGE_USER_TEMPLATE = """用户问题：{question}

已识别槽位：{slots}

候选标准问题：
{candidates}

请选择最匹配的一个候选。只输出 JSON：
{{"entry_id":"候选 id 或 none","confidence":0 到 1 的小数,"reason":"一句话理由"}}
"""


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def judge_match(
    llm: Any,
    model: str,
    question: str,
    candidates: list[dict[str, Any]],
    slot_labels: Optional[dict[str, Any]] = None,
    *,
    max_candidates: int = 3,
) -> Optional[dict[str, Any]]:
    """让 LLM 在 top-N 候选里复核一次；失败返回 None。"""
    if llm is None or not candidates:
        return None
    top = candidates[:max_candidates]
    allowed = {str(item.get("id")) for item in top}
    candidate_lines = []
    for index, item in enumerate(top, start=1):
        candidate_lines.append(
            f"{index}. id={item.get('id')}｜标准问法：{item.get('question')}｜"
            f"口径：{item.get('intent') or ''}｜风险：{item.get('risk') or 'low'}"
        )
    slots = json.dumps(slot_labels or {}, ensure_ascii=False)
    prompt = JUDGE_USER_TEMPLATE.format(
        question=question,
        slots=slots,
        candidates="\n".join(candidate_lines),
    )
    try:
        message = await llm.chat(
            model=model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
        )
    except Exception:
        return None
    parsed = _extract_json(str(getattr(message, "content", "") or ""))
    if not parsed:
        return None
    entry_id = str(parsed.get("entry_id") or "none")
    if entry_id != "none" and entry_id not in allowed:
        return None
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "entry_id": entry_id,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(parsed.get("reason") or "")[:200],
    }
