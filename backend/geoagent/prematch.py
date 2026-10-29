"""标准问题预匹配：先按指标口径命中标准问题并确定性执行，未命中再交给自由 SQL。

对应"统计口径很重要"的四条纪律：

1. **期次先定**：本轮用哪一期套合表由 :mod:`geoagent.datasets` 解析，标准问题 SQL 只写
   ``{table}``；换期只改 datasets.json，不动问题库和代码。
2. **先匹配**：命中标准问题（confidence 达标）时直接按条目的 ``strategy`` 执行——确定性
   工具或条目参考 SQL，数字与口径都不经过模型润色。
3. **后兜底**：未命中、或命中但执行失败，才进入 LLM 工具循环自由查询；命中信息会作为
   提示注入（``GEOAGENT_QA_PREMATCH=hint`` 时只提示、不自动执行）。
4. **未登记期次不猜**：问题里提到某期却没有登记表时直接说明并停止，不用默认期次顶替。

执行结果的事件与落库形状与普通工具轮次完全一致（tool_call / tool_result / artifact /
message），前端无需改动即可渲染。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .core.events import Event
from .core.llm import AssistantMessage, ToolCall
from .datasets import (
    DatasetPeriod,
    DatasetRegistry,
    PeriodLookup,
    bind_entry_sql,
    default_registry,
    describe_periods,
    find_period,
)
from .qa_semantics import (
    caliber_card,
    profile_from_dict,
    semantic_match_enriched,
)
from .qa_verify import verification_text, verify_matched_result
from .report.question_library import load_question_library
from .tools.executor import ToolExecutor
from .tools.labels import humanize_table
from .tools.units import normalize_unit
from .tools.qa_library import resolve_asset_path as resolve_library_asset
from .tools.result import Artifact, ToolResult

# 与报告生成有关的问法不进预匹配：它们要的是"生成文件"，不是某个统计指标。
_REPORT_INTENT = ("快报", "简报", "报告", "生成word", "生成 word", "导出word", "导出 pdf", "pdf")


@dataclass
class _Executed:
    """一次标准问题执行的结果：工具卡片内容 + 正文用的确定性结论。"""

    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    body: str
    unit: str = ""


@lru_cache(maxsize=4)
def _load_library(path: str) -> dict[str, Any]:
    """按路径缓存问题库（每轮问答都读一遍 50KB JSON 没必要）。"""
    return load_question_library(path)


@lru_cache(maxsize=1)
def _executor() -> ToolExecutor:
    """预匹配执行器：只按名字查找确定性统计工具与受控 SQL 工具。"""
    from .tools.chart import get_chart_tools
    from .tools.pg import get_sql_tools
    from .tools.report import get_report_tools
    from .tools.stat import get_stat_tools
    from .tools.warning import get_warning_tools

    return ToolExecutor(
        get_stat_tools()
        + get_sql_tools()
        + get_report_tools()
        + get_chart_tools()
        + get_warning_tools()
    )


def _skills_dir(ctx: Any) -> Optional[str]:
    skills = getattr(ctx, "skills", None)
    return getattr(skills, "skills_dir", None)


def _registry(ctx: Any) -> DatasetRegistry:
    registry = getattr(ctx, "datasets", None)
    return registry if isinstance(registry, DatasetRegistry) else default_registry()


def resolve_turn_dataset(ctx: Any, question: str) -> PeriodLookup:
    """解析本轮期次并写入 ``ctx.dataset`` / ``ctx.period_notice``。

    优先级：前端显式指定的期次 → 问题文本里识别到的期次 → 默认期次。
    """
    registry = _registry(ctx)
    explicit = str(getattr(ctx, "dataset_id", "") or "").strip()
    if explicit:
        period = registry.get(explicit)
        lookup = PeriodLookup(dataset=period or registry.default, raw=explicit)
    else:
        lookup = find_period(registry, question)
    ctx.dataset = lookup.dataset
    if lookup.mentioned and lookup.dataset is None:
        ctx.period_notice = (
            f"问题里提到的期次「{lookup.raw}」还没有登记数据表，无法统计。"
            f"已登记期次：{describe_periods(registry)}。"
            "请确认期次说法，或先把这一期套合表登记到 "
            "skills/qa-library/assets/datasets.json 后再问。"
        )
    else:
        ctx.period_notice = ""
    return lookup


def build_period_prompt(ctx: Any) -> str:
    """给自由 SQL 兜底路径的期次说明（含 hint 模式下的口径提示）。"""
    period = getattr(ctx, "dataset", None)
    lines = ["\n\n## 8. 当前期次与口径纪律"]
    if period is not None:
        lines.append(
            f"- 本轮统计对象：{period.label}，物理表 {period.table}（自由 SQL 必须用这个表名）。"
        )
    lines.append(f"- 已登记期次：{describe_periods(_registry(ctx))}。")
    lines.append(
        "- 一张套合表代表一期数据：不同期次的数字不能混在同一个口径/合计里；"
        "用户要求跨期对比时，必须分别标明期次。"
    )
    lines.append(
        "- SQL 里引用非当前期次的已登记表时，先在回答中说明原因；"
        "不得把其它期次的结果并进当前期次的合计。"
    )
    from .tools.field_dictionaries import prompt_summary

    field_summary = prompt_summary()
    if field_summary:
        lines.append(f"- 字段字典（展示辅助，不替代编码判断）：{field_summary}。")
    hint = str(getattr(ctx, "prematch_hint", "") or "")
    if hint:
        lines.append("")
        lines.append(hint)
    return "\n".join(lines)


def _is_report_intent(question: str) -> bool:
    text = str(question or "").lower()
    return any(word in text for word in _REPORT_INTENT)


def _entry_of(library: dict[str, Any], entry_id: Any) -> Optional[dict[str, Any]]:
    target = str(entry_id or "")
    if not target:
        return None
    for entry in library.get("entries", []):
        if str(entry.get("id")) == target:
            return entry
    return None


def _summarize_rows(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """把查询结果压成确定性结论（明细交给表格卡片）。"""
    if not rows:
        return "查询完成，但当前期次没有返回任何数据（0 行）。请确认期次是否正确、口径是否需要调整。"
    if len(rows) == 1:
        return "；".join(f"{col}：{rows[0].get(col)}" for col in columns)
    label_col = columns[0] if columns else ""
    lines = [f"查询完成，共返回 {len(rows)} 行；完整明细见上方查询结果表。"]
    for row in rows[:3]:
        label = row.get(label_col, "") if label_col else ""
        details = "；".join(
            f"{col}：{row.get(col)}" for col in columns[1:] if row.get(col) is not None
        )
        lines.append(f"- {label}：{details}" if details else f"- {label}")
    if len(rows) > 3:
        lines.append(f"- ……其余 {len(rows) - 3} 行见表格卡片。")
    return "\n".join(lines)


# 行政区名缓存：从当期表 XMC 去重得到。这是"只读参考数据"缓存（与会话状态无关），
# 按表名隔离，换期次自然失效。
_REGION_CACHE: dict[str, list[str]] = {}
_REGION_SUFFIXES = ("县", "市", "区", "镇", "乡")


async def _table_regions(ctx: Any, table: str) -> list[str]:
    """当期表的县级行政区清单（首次查询后缓存）。"""
    if not table:
        return []
    cached = _REGION_CACHE.get(table)
    if cached is not None:
        return cached
    gateway = getattr(ctx, "pg", None)
    if gateway is None:
        return []
    try:
        data = await gateway.run_sql(
            f'SELECT DISTINCT "XMC" AS xmc FROM {table} WHERE "XMC" IS NOT NULL',
            conversation_id="prematch-region",
        )
    except Exception:
        return []
    names = [str(row.get("xmc")) for row in (data.get("rows") or []) if row.get("xmc")]
    if names:
        _REGION_CACHE[table] = names
    return names


def _mentioned_regions(question: str, names: list[str]) -> list[str]:
    """问题里出现的具体行政区。

    只认数据库里真实存在的县名，避免把"每个县""哪些县"这类泛称误判成筛选条件。
    """
    text = str(question or "")
    if not names or not any(suffix in text for suffix in _REGION_SUFFIXES):
        return []
    hits = [name for name in names if name and name in text]
    hits.sort(key=len, reverse=True)
    return hits


async def _scoped_arguments(
    ctx: Any,
    entry: dict[str, Any],
    question: str,
    table: str,
) -> tuple[dict[str, Any], str]:
    """把问题里的具体行政区转成工具参数；标准条目表达不了就给出阻断原因。

    标准条目是整表口径，一旦问题限定了某个县，直接套用会静默统计到全库——
    所以要么把区县作为参数传下去，要么放弃确定性执行、交自由 SQL 兜底。
    """
    regions = _mentioned_regions(question, await _table_regions(ctx, table))
    if not regions:
        return {}, ""
    if len(regions) > 1:
        return {}, (
            "问题里出现了多个行政区（" + "、".join(regions)
            + "），标准条目是整表口径，已转自由 SQL 分别统计。"
        )
    name = regions[0]
    tool_name = str(entry.get("tool") or "")
    if str(entry.get("strategy")) == "tool" and tool_name:
        tool = _executor().tool_map.get(tool_name)
        properties = (tool.parameters or {}).get("properties", {}) if tool else {}
        if "county" in properties:
            return {"county": name}, ""
    return {}, (
        f"问题限定了行政区“{name}”，而标准条目“{entry.get('question')}”是整表口径，"
        "不能直接套用，已转自由 SQL 按该行政区查询。"
    )

async def _execute_entry(
    ctx: Any,
    entry: dict[str, Any],
    arguments: Optional[dict[str, Any]] = None,
) -> Optional[_Executed]:
    """按条目 strategy 执行：确定性工具优先，其次条目参考 SQL。

    ``arguments`` 用于把问题里的额外筛选（如某个区县）传给确定性工具；
    条目表达不了的筛选不会走到这里（见 :func:`_scoped_arguments`）。
    """
    executor = _executor()
    tool_name = str(entry.get("tool") or "")
    registry = _registry(ctx)
    period = getattr(ctx, "dataset", None)

    async def run_sql_fallback() -> Optional[_Executed]:
        raw_sql = str(entry.get("sql") or "").strip()
        if not raw_sql:
            return None
        gateway = getattr(ctx, "pg", None)
        if gateway is None:
            return None
        sql = bind_entry_sql(raw_sql, registry, period)
        tool_call_id = f"std-{uuid4().hex[:8]}"
        try:
            data = await gateway.run_sql(
                sql,
                conversation_id=getattr(ctx, "conversation_id", ""),
                tool_call_id=tool_call_id,
            )
        except Exception:
            return None
        columns, rows = humanize_table(data.get("columns", []), data.get("rows", []))
        truncated = bool(data.get("truncated"))
        note = "（结果已截断至查询上限）" if truncated else ""
        result = ToolResult(
            tool_call_id=tool_call_id,
            name="run_sql",
            content=f"查询返回 {data.get('row_count', len(rows))} 行{note}。",
            artifacts=[
                Artifact(
                    kind="table",
                    name="query_result",
                    data={"columns": columns, "rows": rows},
                )
            ],
        )
        return _Executed(
            tool_name="run_sql",
            arguments={"sql": sql},
            result=result,
            body=_summarize_rows(columns, rows),
            unit=str(entry.get("unit") or ""),
        )

    if str(entry.get("strategy")) == "tool" and tool_name in executor.tool_map:
        tool_arguments = dict(arguments or {})
        tool_call = ToolCall(
            id=f"std-{uuid4().hex[:8]}", name=tool_name, arguments=tool_arguments
        )
        result = await executor.execute(tool_call, ctx=ctx)
        if not result.is_error:
            return _Executed(
                tool_name=tool_name,
                arguments=tool_arguments,
                result=result,
                body=str(result.content or ""),
                unit=str(entry.get("unit") or ""),
            )
        # 确定性工具失败（例如库未接入）时退回条目参考 SQL。
    return await run_sql_fallback()


def _risk_label(risk: str) -> str:
    return {"low": "低", "medium": "中", "high": "高"}.get(str(risk or ""), str(risk or "低"))


def _compose(
    entry: dict[str, Any],
    period: Optional[DatasetPeriod],
    executed: _Executed,
    *,
    caliber: Optional[dict[str, Any]] = None,
    verification: Optional[dict[str, Any]] = None,
) -> str:
    """组装确定性正文：先给结论，再用短行说明口径、期次、范围与校验。"""
    body = str(executed.body or "").strip()
    lines = [body] if body else []
    caliber_text = str(entry.get("intent") or entry.get("answer_hint") or "").strip()
    # 对外只保留用户能看懂的口径，去掉 TBLX/TZ/MJ 等字段名括号。
    caliber_text = re.sub(
        r"[（(][^）)]*(?:TBLX|TZ|DLBM|DLMC|MJ|SFYN|SFHX)[^）)]*[）)]",
        "",
        caliber_text,
    ).strip()
    tail = [f"统计口径：{caliber_text}"] if caliber_text else []
    if period is not None:
        tail.append(f"数据期次：{period.label}")
    if caliber and caliber.get("scope") and str(caliber.get("scope")) != "全库":
        tail.append(f"统计范围：{caliber.get('scope')}")
    if executed.unit:
        tail.append(f"单位：{normalize_unit(executed.unit)}")
    risk = str((caliber or {}).get("risk") or "")
    if risk in {"medium", "high"}:
        tail.append(f"风险等级：{_risk_label(risk)}")
    if tail:
        lines.append("\n".join(tail))
    if risk == "high":
        lines.append("风险提示：该口径只能作为“疑似”线索，违法认定需叠加审批、执法或用地管理数据。")
    elif risk == "medium":
        lines.append("说明：该结果涉及占比/流向/跨类汇总，口径以标准问题条目为准，不与其他期次混算。")
    if verification is not None:
        text = verification_text(verification)
        if text:
            prefix = "⚠ " if not verification.get("ok") else ""
            lines.append(prefix + text)
    return "\n\n".join(lines)


def _slot_sentence(match: dict[str, Any]) -> str:
    """把槽位压成一句中文，供澄清/计划提示复用。"""
    labels = match.get("slot_labels") or {}
    slots = match.get("slots") or {}
    parts: list[str] = []
    if labels.get("subjects"):
        parts.append("主体=" + "、".join(labels["subjects"]))
    if labels.get("filters"):
        parts.append("限定=" + "、".join(labels["filters"]))
    if labels.get("aggregations"):
        parts.append("聚合=" + "、".join(labels["aggregations"]))
    if labels.get("dimensions"):
        parts.append("维度=" + "、".join(labels["dimensions"]))
    side = str(slots.get("side") or "")
    if side:
        parts.append("时项=" + {"before": "变化前", "after": "变化后", "flow": "流入流出",
                               "transition": "转换方向"}.get(side, side))
    if slots.get("transition"):
        parts.append("方向=" + "→".join(str(item) for item in slots["transition"]))
    if slots.get("top_n"):
        parts.append(f"Top-N={slots['top_n']}")
    return "；".join(parts) if parts else "未识别出明确的口径槽位"


def _candidate_lines(match: dict[str, Any], limit: int = 3) -> list[str]:
    lines: list[str] = []
    for index, candidate in enumerate((match.get("candidates") or [])[:limit], start=1):
        question = str(candidate.get("question") or "")
        intent = str(candidate.get("intent") or "").strip()
        lines.append(f"{index}. {question}")
        if intent:
            lines.append(f"   口径：{intent}")
    return lines


def _clarify_reply(match: dict[str, Any]) -> str:
    """低置信/对抗问法的定向澄清：不查库、给 2~3 个可选项。"""
    slots = str((match.get("slots") or {}).get("side") or "")
    lines = ["需要先确认统计口径。"]
    if (match.get("slots") or {}).get("adversarial"):
        lines.append(
            "你的问题里包含与库内口径冲突的要求（例如要求编造、改变单位或直接定性）。"
            "我只能按库内可核验的数据回答，不能按该要求编造或改写口径。"
        )
    else:
        lines.append("我还没有足够把握直接查库，先确认一下你要的统计口径。")
    lines.append("我的理解：" + _slot_sentence(match))
    candidates = _candidate_lines(match)
    if candidates:
        lines.append("最接近的标准问题是：")
        lines.extend(candidates)
    lines.append("请回复序号，或补充期次、区县、变化前/变化后等条件。本次未查询数据库。")
    return "\n\n".join(lines)


def _candidates_reply(match: dict[str, Any]) -> str:
    """多候选接近时让用户选口径，而不是静默转自由 SQL。"""
    lines = [
        "我还不确定你要统计哪一项。",
        "问题比较笼统，最接近的标准问题不止一个。请先选择口径：",
    ]
    lines.extend(_candidate_lines(match))
    lines.append("回复序号即可；也可以把问题说得更具体（例如“变化前耕地面积、按县统计”）。本次未查询数据库。")
    return "\n\n".join(lines)


def _understanding_hint(match: dict[str, Any]) -> str:
    """未自动执行时给自由 SQL 的“理解计划”，让降级不再是无感知的。"""
    lines = [
        "### 预匹配理解（未自动执行，供自由 SQL 参考）",
        f"- 识别槽位：{_slot_sentence(match)}",
    ]
    best_id = str(match.get("matched_question_id") or "")
    if best_id:
        lines.append(
            f"- 最接近条目：{best_id}（匹配分 {float(match.get('score') or 0):.3f}，"
            f"执行阈值 {float(match.get('execute_threshold') or 0):.3f}）"
        )
    candidates = _candidate_lines(match)
    if candidates:
        lines.append("- 候选条目：")
        lines.extend("  " + item for item in candidates)
    lines.append(
        "- 若用户问法与上述条目一致，优先按条目口径；否则按自由 SQL 查询，并在正文声明假设与期次。"
    )
    return "\n".join(lines)


def _audit_match(
    ctx: Any,
    question: str,
    match: dict[str, Any],
    *,
    executed: Optional[_Executed] = None,
    verification: Optional[dict[str, Any]] = None,
    elapsed_ms: float = 0.0,
) -> None:
    """把匹配决策写入本地 JSONL，供效果评估与复盘（不含模型/数据库凭据）。"""
    settings = getattr(ctx, "settings", None)
    data_dir = getattr(settings, "data_dir", None)
    if data_dir is None:
        return
    try:
        path = Path(data_dir) / "qa_match_audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "conversation_id": str(getattr(ctx, "conversation_id", "") or ""),
            "question": str(question or "")[:500],
            "resolved_question": str(match.get("resolved_question") or "")[:500],
            "dataset_id": str(getattr(getattr(ctx, "dataset", None), "id", "") or ""),
            "data_version": str(getattr(getattr(ctx, "dataset", None), "version", "") or ""),
            "decision": match.get("decision"),
            "entry_id": match.get("matched_question_id"),
            "score": match.get("score"),
            "confidence": match.get("confidence"),
            "p_correct": match.get("p_correct"),
            "calibrated": bool(match.get("calibrated")),
            "margin": match.get("margin"),
            "risk": match.get("risk"),
            "execute_threshold": match.get("execute_threshold"),
            "vector_used": bool(match.get("vector_used")),
            "vector_entries": int(match.get("vector_entries") or 0),
            "judge_used": bool(match.get("judge_used")),
            "judge_entry": (match.get("judge") or {}).get("entry_id"),
            "judge_confidence": (match.get("judge") or {}).get("confidence"),
            "slots": match.get("slots"),
            "candidates": [item.get("id") for item in (match.get("candidates") or [])],
            "executed_tool": executed.tool_name if executed else "",
            "verification_ok": verification.get("ok") if verification else None,
            "elapsed_ms": round(elapsed_ms, 2),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return


def _hint_block(ctx: Any, match: dict[str, Any], library: dict[str, Any]) -> str:
    entry = _entry_of(library, match.get("matched_question_id"))
    if entry is None:
        return ""
    sql = str(entry.get("sql") or "").strip()
    if sql:
        sql = bind_entry_sql(sql, _registry(ctx), getattr(ctx, "dataset", None))
    action = (
        f"调用确定性工具 {entry.get('tool')}()"
        if str(entry.get("strategy")) == "tool" and entry.get("tool")
        else "用 run_sql 执行下列参考 SQL（可按用户要求调整维度）"
    )
    lines = [
        "### 已命中标准问题（优先采用，但以用户问法为准）",
        f"- 条目：{entry.get('id')}｜{entry.get('question')}",
        f"- 口径：{entry.get('answer_hint') or entry.get('intent') or ''}",
        f"- 推荐执行：{action}",
    ]
    if sql:
        lines.append(f"- 参考 SQL：{sql}")
    lines.append("若用户的要求与该口径有出入，按用户要求查询并说明差异，不要硬套标准口径。")
    return "\n".join(lines)


async def _emit_tool_events(ctx: Any, executed: _Executed) -> None:
    await ctx.emit(
        Event(
            "tool_call",
            {
                "id": executed.result.tool_call_id,
                "name": executed.tool_name,
                "arguments": executed.arguments,
            },
        )
    )
    await ctx.emit(
        Event(
            "tool_result",
            {
                "id": executed.result.tool_call_id,
                "name": executed.tool_name,
                "is_error": bool(executed.result.is_error),
                "content": str(executed.result.content or "")[:2000],
            },
        )
    )
    for artifact in executed.result.artifacts:
        await ctx.emit(Event("artifact", artifact.to_dict()))


def _final_message(
    ctx: Any,
    content: str,
    *,
    metadata: Optional[dict[str, Any]] = None,
) -> AssistantMessage:
    final = AssistantMessage(content=content, model=str(getattr(ctx, "model", "") or ""))
    final_dict = final.to_dict()
    final_dict["route"] = getattr(ctx, "route", "") or "sql"
    final_dict["answer_origin"] = "standard_question"
    if metadata:
        final_dict.update(metadata)
    ctx.session.add_message(final_dict)
    return final


async def _emit_message(
    ctx: Any,
    final: AssistantMessage,
    *,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    payload = {
        "role": "assistant",
        "content": final.content,
        "model": final.model,
        "route": getattr(ctx, "route", "") or "sql",
        "answer_origin": "standard_question",
    }
    if metadata:
        payload.update(metadata)
    await ctx.emit(Event("message", payload))


async def _respond_text(
    ctx: Any,
    question: str,
    content: str,
    *,
    metadata: Optional[dict[str, Any]] = None,
) -> AssistantMessage:
    """不走工具、直接给一段确定性说明（例如期次未登记、口径澄清）。"""
    ctx.session.add_user(question)
    final = _final_message(ctx, content, metadata=metadata)
    await _emit_message(ctx, final, metadata=metadata)
    return final


async def try_standard_answer(
    ctx: Any,
    question: str,
    *,
    match_text: Optional[str] = None,
    original_question: Optional[str] = None,
) -> Optional[AssistantMessage]:
    """按“理解 → 决策 → 执行 → 校验”处理一轮问题。

    ``question`` 是用户原话（用于会话/审计）；``match_text`` 是追问补全后的问题
    （用于期次与标准问题匹配，缺省等于原话）。
    """
    display_question = str(original_question or question)
    match_question = str(match_text or question)
    settings = getattr(ctx, "settings", None)
    mode = str(getattr(settings, "qa_prematch", "execute") or "execute").strip().lower()
    if mode not in {"execute", "hint"}:
        return None
    if getattr(ctx, "followup_notice", ""):
        return await _respond_text(ctx, display_question, str(ctx.followup_notice))
    if getattr(ctx, "period_notice", ""):
        return await _respond_text(ctx, display_question, str(ctx.period_notice))
    if _is_report_intent(match_question):
        return None
    try:
        library = _load_library(str(resolve_library_asset(_skills_dir(ctx))))
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    threshold = float(getattr(settings, "qa_prematch_confidence", 0.42) or 0.42)
    started = time.perf_counter()
    try:
        embedder = getattr(ctx, "embeddings", None) if getattr(settings, "qa_vector", True) else None
        match = await semantic_match_enriched(
            match_question,
            library,
            min_confidence=threshold,
            skills_dir=_skills_dir(ctx),
            embedder=embedder,
            llm=getattr(ctx, "llm", None),
            model=str(getattr(ctx, "model", "") or ""),
            judge=getattr(settings, "qa_judge", True),
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    match["resolved_question"] = match_question
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    period = getattr(ctx, "dataset", None)
    candidates = match.get("candidates") or []
    entry = _entry_of(library, match.get("matched_question_id"))
    best = candidates[0] if candidates else None
    profile = profile_from_dict((best or {}).get("semantic", {}).get("profile") or {})
    info = {
        "mode": mode,
        "entry_id": str(match.get("matched_question_id") or ""),
        "confidence": match.get("confidence"),
        "p_correct": match.get("p_correct"),
        "calibrated": bool(match.get("calibrated")),
        "score": match.get("score"),
        "margin": match.get("margin"),
        "decision": match.get("decision"),
        "risk": match.get("risk"),
        "threshold": match.get("execute_threshold"),
        "p_threshold": match.get("p_threshold"),
        "vector_used": bool(match.get("vector_used")),
        "vector_entries": int(match.get("vector_entries") or 0),
        "judge_used": bool(match.get("judge_used")),
        "judge_entry": (match.get("judge") or {}).get("entry_id"),
        "judge_confidence": (match.get("judge") or {}).get("confidence"),
        "judge_reason": (match.get("judge") or {}).get("reason"),
        "reasons": list(match.get("match_reasons") or []),
        "slots": match.get("slots") or {},
        "slot_labels": match.get("slot_labels") or {},
        "candidates": [str(item.get("id")) for item in candidates],
        "candidate_details": [
            {
                "id": item.get("id"),
                "question": item.get("question"),
                "intent": item.get("intent"),
                "score": item.get("score"),
                "p_correct": item.get("p_correct"),
                "calibrated": item.get("calibrated"),
                "risk": item.get("risk"),
            }
            for item in candidates
        ],
        "dataset_id": getattr(period, "id", "") or "",
        "dataset_label": getattr(period, "label", "") or "",
        "table": getattr(period, "table", "") or "",
        "data_version": getattr(period, "version", "") or "",
        "resolved_question": match_question if match_question != display_question else "",
    }

    if mode == "hint":
        blocks = [_hint_block(ctx, match, library), _understanding_hint(match)]
        ctx.prematch_hint = "\n\n".join(block for block in blocks if block)
        await ctx.emit(
            Event(
                "standard_match",
                {
                    **info,
                    "matched": False,
                    "hint_only": True,
                    "entry_question": (entry or {}).get("question", ""),
                },
            )
        )
        _audit_match(ctx, question, match, elapsed_ms=elapsed_ms)
        return None

    decision = str(match.get("decision") or "fallback")
    if decision in {"clarify", "candidates"}:
        notice = str(match.get("notice") or "")
        await ctx.emit(
            Event(
                "standard_match",
                {**info, "matched": False, "notice": notice},
            )
        )
        content = _clarify_reply(match) if decision == "clarify" else _candidates_reply(match)
        _audit_match(ctx, question, match, elapsed_ms=elapsed_ms)
        return await _respond_text(
            ctx,
            display_question,
            content,
            metadata={
                "match_decision": decision,
                "matched_entry_id": info["entry_id"],
                "slots": info["slots"],
            },
        )

    if decision != "execute" or entry is None:
        ctx.prematch_hint = _understanding_hint(match)
        await ctx.emit(Event("standard_match", {**info, "matched": False}))
        _audit_match(ctx, question, match, elapsed_ms=elapsed_ms)
        return None

    # 问题里限定了具体区县时：能作为工具参数下钻就下钻，否则不套用整表口径
    arguments, blocked = await _scoped_arguments(
        ctx, entry, match_question, str(getattr(period, "table", "") or "")
    )
    if blocked:
        await ctx.emit(
            Event("standard_match", {**info, "matched": False, "notice": blocked})
        )
        _audit_match(ctx, question, match, elapsed_ms=elapsed_ms)
        return None

    caliber = caliber_card(entry, profile, period=period, arguments=arguments)
    if not profile.risk:
        caliber["risk"] = str(match.get("risk") or "low")
    await ctx.emit(
        Event(
            "standard_match",
            {
                **info,
                "matched": True,
                "strategy": entry.get("strategy"),
                "entry_question": entry.get("question", ""),
                "arguments": arguments or {},
                "caliber": caliber,
            },
        )
    )
    executed = await _execute_entry(ctx, entry, arguments)
    if executed is None:
        # 命中但执行失败（库未接入 / SQL 报错）→ 交回自由 SQL 兜底，不编数字。
        ctx.prematch_hint = (
            "### 标准条目执行失败\n"
            f"- 已命中条目：{entry.get('id')}｜{entry.get('question')}\n"
            "- 标准口径执行失败（数据库未接入或 SQL 报错），请改用自由 SQL 并说明失败原因，不要编造数字。"
        )
        await ctx.emit(
            Event(
                "standard_match",
                {**info, "matched": False, "execution_failed": True},
            )
        )
        _audit_match(ctx, question, match, elapsed_ms=elapsed_ms)
        return None

    county = str((arguments or {}).get("county") or "")
    verification = verify_matched_result(entry, executed.result, county=county)
    ctx.session.add_user(display_question)
    ctx.session.add_message(
        {
            "role": "assistant",
            "content": "",
            "model": str(getattr(ctx, "model", "") or ""),
            "route": getattr(ctx, "route", "") or "sql",
            "tool_calls": [
                {
                    "id": executed.result.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": executed.tool_name,
                        "arguments": json.dumps(executed.arguments, ensure_ascii=False),
                    },
                }
            ],
        }
    )
    ctx.session.add_message(executed.result.to_message())
    await _emit_tool_events(ctx, executed)
    from .dialogue import remember_query_state

    remember_query_state(
        ctx,
        display_question,
        match_question,
        entry_id=str(entry.get("id") or ""),
        county=county,
        unit=str(entry.get("unit") or ""),
    )
    metadata = {
        "match_decision": "execute",
        "caliber": caliber,
        "verification": verification,
        "query_state": dict(ctx.query_state),
    }
    final = _final_message(
        ctx,
        _compose(entry, period, executed, caliber=caliber, verification=verification),
        metadata=metadata,
    )
    await _emit_message(ctx, final, metadata=metadata)
    _audit_match(
        ctx,
        question,
        match,
        executed=executed,
        verification=verification,
        elapsed_ms=elapsed_ms,
    )
    return final