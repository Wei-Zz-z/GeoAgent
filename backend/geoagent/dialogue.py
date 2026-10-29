"""多轮追问补全：把“那第二期呢 / 这个县呢 / 再按类型分”补成完整问题。

只在“本轮问题很短、且明显是追问”时启用，避免把正常新问题误改写。补全后的
``effective_question`` 用于期次解析与标准问题匹配；用户原话仍写回会话与回答。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .datasets import DatasetRegistry, default_registry, find_period
from .qa_judge import _extract_json

_GENERIC_COUNTY = {
    "每个县", "各县", "各区县", "区县", "县区", "市县", "行政区", "县级",
    "城市", "地区", "市区", "地方",
}
_SUBJECTS = (
    "永久基本农田", "生态保护红线", "建设用地", "耕地", "林地", "草地",
    "园地", "永农", "红线",
)
_COUNTY_RE = re.compile(r"([\u4e00-\u9fff]{2,6}(?:县|区|市))")
_PERIOD_RE = re.compile(
    r"(?:那|那么)?(?:看|换到|换成)?\s*(20\d{2}\s*年?)?\s*第?\s*([0-9一二三四五六七八九十]{1,2})\s*期"
    r"(?:呢|怎么样|如何|是多少)?"
)
_DIMENSION_RULES = (
    ("type", re.compile(r"^(?:再|重新)?(?:按|分)(?:图斑)?类型(?:分|统计|汇总)?(?:一下|看看)?$")),
    ("county", re.compile(r"^(?:再|重新)?(?:按|分)(?:县|区|行政区)(?:分|统计|汇总)?(?:一下|看看)?$")),
    ("land_class", re.compile(r"^(?:再|重新)?(?:按|分)(?:地类|土地类型)(?:分|统计|汇总)?(?:一下|看看)?$")),
)
_DIMENSION_PHRASE = {
    "type": "按图斑类型统计",
    "county": "按行政区统计",
    "land_class": "按地类统计",
}


def _registry(ctx: Any) -> DatasetRegistry:
    registry = getattr(ctx, "datasets", None)
    return registry if isinstance(registry, DatasetRegistry) else default_registry()


def _strip_periods(text: str, registry: DatasetRegistry) -> str:
    """去掉上一轮问题里的期次说法，避免“第一期 + 第二期”同时出现。"""
    cleaned = str(text or "")
    phrases: set[str] = set()
    for period in registry.periods:
        phrases.add(period.label)
        phrases.update(period.aliases)
    for phrase in sorted(phrases, key=len, reverse=True):
        if phrase:
            cleaned = cleaned.replace(phrase, "")
    cleaned = re.sub(
        r"20\d{2}\s*年?\s*第?\s*[0-9一二三四五六七八九十]{1,2}\s*期", "", cleaned
    )
    return cleaned.strip(" ，,。；;、")


def _infer_county(text: str) -> str:
    for match in _COUNTY_RE.finditer(str(text or "")):
        name = match.group(1)
        if name not in _GENERIC_COUNTY and not name.startswith(("每个", "各", "该", "这个", "那个")):
            return name
    return ""


def _infer_dimension(text: str) -> str:
    for dimension, pattern in _DIMENSION_RULES:
        if pattern.match(str(text or "").strip()):
            return dimension
    if any(word in str(text) for word in ("按类型", "分类型", "每种类型", "各类型")):
        return "type"
    if any(word in str(text) for word in ("按县", "分县", "每个县", "各县", "行政区")):
        return "county"
    if any(word in str(text) for word in ("按地类", "分地类", "各地类")):
        return "land_class"
    return ""


def _replace_county(text: str, county: str) -> str:
    if county and county in text:
        return text
    if _COUNTY_RE.search(text):
        return _COUNTY_RE.sub(county, text, count=1)
    return f"{text}，{county}"


def _replace_subject(text: str, subject: str) -> str:
    for candidate in _SUBJECTS:
        if candidate in text and candidate != subject:
            return text.replace(candidate, subject, 1)
    return f"{text}，{subject}"


def _state_from_history(ctx: Any) -> dict[str, Any]:
    """从已恢复的会话历史里找上一轮 QueryState（跨 REST/WS 请求）。"""
    session = getattr(ctx, "session", None)
    history = session.history() if session is not None else []
    for message in reversed(history):
        state = message.get("query_state")
        if isinstance(state, dict) and state.get("effective_question"):
            return dict(state)
    # 兼容旧会话：没有 query_state 时至少用上一条用户消息作为上下文。
    for message in reversed(history):
        if message.get("role") == "user" and str(message.get("content") or "").strip():
            question = str(message["content"])
            return {
                "last_user": question,
                "last_question": question,
                "effective_question": question,
                "county": _infer_county(question),
                "dimension": _infer_dimension(question),
            }
    return {}


def resolve_followup(ctx: Any, question: str) -> tuple[str, dict[str, Any]]:
    """返回（补全后的问题，追问信息）；不是追问时原样返回。"""
    text = str(question or "").strip()
    state = dict(getattr(ctx, "query_state", None) or {})
    if not state.get("effective_question"):
        state = _state_from_history(ctx) or state
        if state:
            ctx.query_state = dict(state)
    previous = str(state.get("effective_question") or state.get("last_question") or "")
    if not previous or len(text) > 40:
        return question, {}

    registry = _registry(ctx)
    # 1) 期次追问：“那第二期呢”
    period_match = _PERIOD_RE.fullmatch(text)
    if period_match:
        phrase = period_match.group(0)
        phrase = re.sub(r"^(?:那|那么|看|换到|换成)\s*", "", phrase)
        phrase = re.sub(r"(?:呢|怎么样|如何|是多少)$", "", phrase).strip()
        lookup = find_period(registry, phrase)
        base = _strip_periods(previous, registry)
        effective = f"{base}{phrase}" if base else phrase
        if lookup.dataset is not None:
            # 用户在本轮明确说了期次，优先于前端上一轮选择的数据表。
            ctx.dataset_id = lookup.dataset.id
            return effective, {"kind": "period", "period_id": lookup.dataset.id,
                               "rewritten": effective}
        ctx.dataset_id = ""
        return effective, {"kind": "period_missing", "rewritten": effective}

    # 2) 区县指代：“这个县呢 / 该县呢”
    if re.search(r"(这个|那个|该|上述|此)(县|区|市|地方)", text):
        county = str(state.get("county") or "")
        if not county:
            notice = "你说的“这个县”是指哪个县？请补一个县名（如“缙云县”）。"
            ctx.followup_notice = notice
            return question, {"kind": "county_missing", "notice": notice}
        effective = _replace_county(previous, county)
        return effective, {"kind": "county", "county": county, "rewritten": effective}

    # 3) 只给县名：“缙云县呢”
    county_match = re.fullmatch(
        r"(?:那|那么)?([\u4e00-\u9fff]{2,6}(?:县|区|市))(?:呢|怎么样|如何|是多少)?", text
    )
    if county_match and county_match.group(1) not in _GENERIC_COUNTY:
        county = county_match.group(1)
        effective = _replace_county(previous, county)
        return effective, {"kind": "county", "county": county, "rewritten": effective}

    # 4) 维度追问：“再按类型分 / 分县”
    for dimension, pattern in _DIMENSION_RULES:
        if pattern.match(text):
            effective = f"{previous}，{_DIMENSION_PHRASE[dimension]}"
            return effective, {"kind": "dimension", "dimension": dimension,
                               "rewritten": effective}

    # 5) 主体追问：“那林地呢 / 建设用地呢”
    subject_match = re.fullmatch(
        r"(?:那|那么)?(永久基本农田|生态保护红线|建设用地|耕地|林地|草地|园地|永农|红线)"
        r"(?:呢|怎么样|如何|是多少)?",
        text,
    )
    if subject_match:
        subject = subject_match.group(1)
        effective = _replace_subject(previous, subject)
        return effective, {"kind": "subject", "subject": subject, "rewritten": effective}

    # 6) 继续/再看看
    if text in {"继续", "然后呢", "再看看", "还有呢"}:
        return previous, {"kind": "continue", "rewritten": previous}

    return question, {}


def remember_query_state(
    ctx: Any,
    question: str,
    effective_question: str,
    *,
    entry_id: str = "",
    county: str = "",
    unit: str = "",
) -> None:
    """一轮结束后更新轻量 QueryState，供下一轮追问继承。"""
    state = dict(getattr(ctx, "query_state", None) or {})
    effective = str(effective_question or question or "")
    state.update(
        {
            "last_user": str(question or ""),
            "last_question": effective,
            "effective_question": effective,
            "dataset_id": str(getattr(getattr(ctx, "dataset", None), "id", "") or ""),
            "entry_id": str(entry_id or state.get("entry_id") or ""),
            "county": str(county or _infer_county(effective) or state.get("county") or ""),
            "dimension": str(_infer_dimension(effective) or state.get("dimension") or ""),
            "subject": str(next((item for item in _SUBJECTS if item in effective), "")),
            "unit": str(unit or getattr(ctx, "display_unit", "") or "亩"),
        }
    )
    ctx.query_state = state


def followup_hint(ctx: Any) -> str:
    """给自由 SQL 的追问补全说明，避免模型只看“那第二期呢”。"""
    followup = getattr(ctx, "followup", None) or {}
    if not followup.get("rewritten"):
        return ""
    state = getattr(ctx, "query_state", None) or {}
    return "\n".join(
        [
            "### 本轮追问补全（多轮指代）",
            f"- 用户本轮原话：{followup.get('last_user') or ''}",
            f"- 上一轮问题：{state.get('last_question') or ''}",
            f"- 本轮补全为：{followup.get('rewritten')}",
            "- 查询与回答必须按补全后的问题理解；如与用户原话冲突，以补全后的问题为准。",
        ]
    )


_FOLLOWUP_CUES = ("那", "呢", "再", "它", "这个", "那个", "换成", "比", "继续", "然后")


async def resolve_followup_smart(
    ctx: Any, question: str
) -> tuple[str, dict[str, Any]]:
    """先规则补全；规则覆盖不了的短追问再用 LLM 做结构化指代消解。

    LLM 只产出 resolved_question 与变更字段，不执行 SQL、不做业务判断；
    解析失败/置信度低时完全回退到规则结果。
    """
    effective, info = resolve_followup(ctx, question)
    if info.get("kind"):
        return effective, info
    text = str(question or "").strip()
    state = dict(getattr(ctx, "query_state", None) or {})
    previous = str(state.get("effective_question") or "")
    llm = getattr(ctx, "llm", None)
    if (
        llm is None
        or not previous
        or len(text) > 40
        or not any(cue in text for cue in _FOLLOWUP_CUES)
    ):
        return effective, info
    prompt = (
        "把用户本轮追问补全成一个完整问题。只做指代消解，不要执行查询、不要编造数字。\n"
        "只输出 JSON："
        '{"is_followup":true,"resolved_question":"完整问题",'
        '"changed_fields":["period|county|dimension|subject|unit|other"],"confidence":0.0}\n\n'
        f"上一轮完整问题：{previous}\n"
        f"上一轮状态：{json.dumps(state, ensure_ascii=False)}\n"
        f"本轮原话：{text}"
    )
    try:
        message = await llm.chat(
            model=str(getattr(ctx, "model", "") or ""),
            messages=[
                {"role": "system", "content": "你是多轮地理统计问答的指代消解器。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        parsed = _extract_json(str(getattr(message, "content", "") or ""))
    except Exception:
        return effective, info
    if not isinstance(parsed, dict) or not parsed.get("is_followup"):
        return effective, info
    resolved = str(parsed.get("resolved_question") or "").strip()
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not resolved or resolved == text or len(resolved) > 160 or confidence < 0.6:
        return effective, info
    fields = parsed.get("changed_fields")
    fields = [str(item) for item in fields] if isinstance(fields, list) else []
    if "period" in fields:
        # 用户在本轮明确换了期次，不能被前端上一轮的数据表选择覆盖。
        ctx.dataset_id = ""
    return resolved, {
        "kind": "structured",
        "rewritten": resolved,
        "confidence": confidence,
        "fields": fields,
    }
