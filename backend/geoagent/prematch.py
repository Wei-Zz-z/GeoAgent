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
from dataclasses import dataclass, field
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
from .report.question_library import load_question_library, match_question
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


def _compose(entry: dict[str, Any], period: Optional[DatasetPeriod], executed: _Executed) -> str:
    """组装确定性正文：先给结论，再写清口径与期次（可追溯）。"""
    lines = [f"## {entry.get('question', '标准问题')}", str(executed.body or "").strip()]
    caliber = str(entry.get("answer_hint") or entry.get("intent") or "").strip()
    scope = ""
    if period is not None:
        scope = f"数据期次：{period.label}（{period.table}）"
    tail = [
        item
        for item in [
            f"统计口径：{caliber}" if caliber else "",
            scope,
            f"单位：{normalize_unit(executed.unit)}" if executed.unit else "",
        ]
        if item
    ]
    if tail:
        lines.append("；".join(tail) + "。")
    lines.append(
        f"（本回答由标准问题库条目 {entry.get('id')} 直接执行，未经过自由 SQL。）"
    )
    return "\n\n".join(lines)


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


def _final_message(ctx: Any, content: str) -> AssistantMessage:
    final = AssistantMessage(content=content, model=str(getattr(ctx, "model", "") or ""))
    final_dict = final.to_dict()
    final_dict["route"] = getattr(ctx, "route", "") or "sql"
    final_dict["answer_origin"] = "standard_question"
    ctx.session.add_message(final_dict)
    return final


async def _emit_message(ctx: Any, final: AssistantMessage) -> None:
    await ctx.emit(
        Event(
            "message",
            {
                "role": "assistant",
                "content": final.content,
                "model": final.model,
                "route": getattr(ctx, "route", "") or "sql",
                "answer_origin": "standard_question",
            },
        )
    )


async def _respond_text(ctx: Any, question: str, content: str) -> AssistantMessage:
    """不走工具、直接给一段确定性说明（例如期次未登记）。"""
    ctx.session.add_user(question)
    final = _final_message(ctx, content)
    await _emit_message(ctx, final)
    return final


async def try_standard_answer(ctx: Any, question: str) -> Optional[AssistantMessage]:
    """命中标准问题就确定性作答；未命中返回 None（由自由 SQL 兜底）。"""
    settings = getattr(ctx, "settings", None)
    mode = str(getattr(settings, "qa_prematch", "execute") or "execute").strip().lower()
    if mode not in {"execute", "hint"}:
        return None
    if getattr(ctx, "period_notice", ""):
        return await _respond_text(ctx, question, str(ctx.period_notice))
    if _is_report_intent(question):
        return None
    try:
        library = _load_library(str(resolve_library_asset(_skills_dir(ctx))))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    threshold = float(getattr(settings, "qa_prematch_confidence", 0.5) or 0.5)
    match = match_question(str(question), library, min_confidence=threshold)
    period = getattr(ctx, "dataset", None)
    info = {
        "mode": mode,
        "entry_id": str(match.get("matched_question_id") or ""),
        "confidence": match.get("confidence"),
        "reasons": list(match.get("match_reasons") or []),
        "candidates": [str(c.get("id")) for c in (match.get("candidates") or [])],
        "dataset_id": getattr(period, "id", "") or "",
        "dataset_label": getattr(period, "label", "") or "",
        "table": getattr(period, "table", "") or "",
    }
    confident = (
        match.get("status") == "candidate_found" and bool(match.get("matched_question_id"))
    )
    entry = _entry_of(library, match.get("matched_question_id")) if confident else None

    if mode == "hint":
        ctx.prematch_hint = _hint_block(ctx, match, library)
        await ctx.emit(
            Event(
                "standard_match",
                {**info, "matched": False, "hint_only": True, "entry_question": (entry or {}).get("question", "")},
            )
        )
        return None

    if entry is None:
        await ctx.emit(Event("standard_match", {**info, "matched": False}))
        return None

    # 问题里限定了具体区县时：能作为工具参数下钻就下钻，否则不套用整表口径
    arguments, blocked = await _scoped_arguments(
        ctx, entry, str(question), str(getattr(period, "table", "") or "")
    )
    if blocked:
        await ctx.emit(
            Event("standard_match", {**info, "matched": False, "notice": blocked})
        )
        return None

    await ctx.emit(
        Event(
            "standard_match",
            {
                **info,
                "matched": True,
                "strategy": entry.get("strategy"),
                "entry_question": entry.get("question", ""),
                "arguments": arguments or {},
            },
        )
    )
    executed = await _execute_entry(ctx, entry, arguments)
    if executed is None:
        # 命中但执行失败（库未接入 / SQL 报错）→ 交回自由 SQL 兜底，不编数字。
        return None
    ctx.session.add_user(str(question))
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
    final = _final_message(ctx, _compose(entry, period, executed))
    await _emit_message(ctx, final)
    return final