"""数字溯源（number_guard）与多轮追问补全的回归用例。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoagent.agents.sql import SQLAgent
from geoagent.core.llm import AssistantMessage
from geoagent.datasets import DatasetPeriod, DatasetRegistry
from geoagent.dialogue import remember_query_state, resolve_followup
from geoagent.number_guard import verify_numbers, warning_text


def _tool(content: str = "", artifacts=None):
    message = {"role": "tool", "content": content}
    if artifacts:
        message["artifacts"] = artifacts
    return message


def test_number_guard_accepts_direct_values_and_mu_conversion():
    messages = [
        _tool(
            "全库合计：164798 个图斑，面积 20586122.73 平方米",
            artifacts=[
                {
                    "kind": "table",
                    "name": "summary",
                    "data": {"columns": ["图斑数", "面积(亩)"], "rows": [["164798", "30879.18"]]},
                }
            ],
        )
    ]
    result = verify_numbers(
        "全库共有 164,798 个图斑，总面积 30,879.18 亩。",
        messages,
        question="全库共有多少个变化图斑、总面积是多少",
    )
    assert result["ok"] is True, result


def test_number_guard_accepts_derived_percentage_but_rejects_fabricated_one():
    messages = [
        _tool(
            "建设用地 2954687.33 平方米；总面积 20586122.73 平方米",
            artifacts=[
                {
                    "kind": "table",
                    "name": "ratio",
                    "data": {
                        "columns": ["建设用地面积", "总面积"],
                        "rows": [["2954687.33", "20586122.73"]],
                    },
                }
            ],
        )
    ]
    good = verify_numbers("建设用地占比 14.35%。", messages)
    assert good["ok"] is True, good
    bad = verify_numbers("建设用地占比 18.2%。", messages)
    assert bad["ok"] is False
    assert "18.2" in warning_text(bad)


def test_number_guard_allows_question_parameters_and_ignores_codes():
    result = verify_numbers(
        "筛选 DLBM 为 01 开头的图斑，面积小于 100 平方米的共有 3 个。",
        [_tool("count=3")],
        question="面积小于 100 平方米的细碎图斑有多少",
    )
    # 100 来自用户问题；01 是编码；3 来自工具结果。
    assert result["ok"] is True, result


def test_number_guard_flags_number_without_evidence():
    result = verify_numbers("新增建设用地约 999 亩。", [_tool("count=1")])
    assert result["ok"] is False
    assert result["unverified"][0]["value"] == 999.0


def _ctx() -> SimpleNamespace:
    registry = DatasetRegistry(
        periods=(
            DatasetPeriod(id="2026_1", label="2026年第一期", table='data."t1"', year=2026, round=1),
            DatasetPeriod(id="2026_2", label="2026年第二期", table='data."t2"', year=2026, round=2),
        ),
        default_id="2026_1",
    )
    return SimpleNamespace(
        datasets=registry,
        dataset=registry.periods[0],
        dataset_id="",
        query_state={},
        followup_notice="",
    )


def test_followup_period_rewrites_previous_question_and_switches_dataset():
    ctx = _ctx()
    remember_query_state(
        ctx,
        "2026年第一期耕地面积是多少",
        "2026年第一期耕地面积是多少",
        entry_id="orig_farmland",
    )
    effective, info = resolve_followup(ctx, "那第二期呢")
    assert info["kind"] == "period"
    assert info["period_id"] == "2026_2"
    assert "第二期" in effective
    assert "第一期" not in effective
    assert ctx.dataset_id == "2026_2"


def test_followup_county_pronoun_uses_last_county_or_asks():
    ctx = _ctx()
    remember_query_state(ctx, "统计缙云县耕地面积", "统计缙云县耕地面积", county="缙云县")
    effective, info = resolve_followup(ctx, "这个县呢")
    assert info["kind"] == "county"
    assert "缙云县" in effective

    empty = _ctx()
    remember_query_state(empty, "耕地面积是多少", "耕地面积是多少")
    effective, info = resolve_followup(empty, "这个县呢")
    assert info["kind"] == "county_missing"
    assert empty.followup_notice
    assert effective == "这个县呢"


def test_followup_dimension_and_subject_rewrites():
    ctx = _ctx()
    remember_query_state(ctx, "耕地面积是多少", "耕地面积是多少")
    effective, info = resolve_followup(ctx, "再按类型分")
    assert info["kind"] == "dimension"
    assert "按图斑类型统计" in effective

    effective, info = resolve_followup(ctx, "那林地呢")
    assert info["kind"] == "subject"
    assert "林地" in effective
    assert "耕地" not in effective


def test_followup_reads_query_state_from_history_when_context_is_new():
    ctx = _ctx()
    ctx.session = SimpleNamespace(
        history=lambda: [
            {"role": "user", "content": "2026年第一期耕地面积是多少"},
            {
                "role": "assistant",
                "content": "全库合计……",
                "query_state": {
                    "effective_question": "2026年第一期耕地面积是多少",
                    "last_question": "2026年第一期耕地面积是多少",
                    "dataset_id": "2026_1",
                    "county": "",
                },
            },
        ]
    )
    effective, info = resolve_followup(ctx, "那第二期呢")
    assert info["kind"] == "period"
    assert "第二期" in effective
    assert ctx.dataset_id == "2026_2"


def test_remember_query_state_keeps_county_and_dataset():
    ctx = _ctx()
    ctx.dataset = ctx.datasets.periods[1]
    remember_query_state(
        ctx,
        "缙云县耕地面积",
        "缙云县耕地面积",
        entry_id="orig_farmland",
        county="缙云县",
    )
    assert ctx.query_state["county"] == "缙云县"
    assert ctx.query_state["dataset_id"] == "2026_2"
    assert ctx.query_state["entry_id"] == "orig_farmland"


@pytest.mark.asyncio
async def test_sql_agent_finalize_blocks_unfixable_numbers_without_llm():
    ctx = _ctx()
    ctx.current_question = "建设用地占比是多少"
    ctx.effective_question = "建设用地占比是多少"
    ctx.turn_tool_messages = [
        _tool("建设用地 2954687.33 平方米；总面积 20586122.73 平方米")
    ]
    agent = SQLAgent()
    final = AssistantMessage(content="建设用地占比 18.2%。", model="test")
    result = await agent.finalize_answer(ctx, final)
    guard = ctx.answer_metadata["number_guard"]
    assert guard["ok"] is False
    assert guard["blocked"] is True
    assert "无法与查询结果对应" in result.content
    assert ctx.answer_metadata["query_state"]["effective_question"] == "建设用地占比是多少"


@pytest.mark.asyncio
async def test_sql_agent_finalize_corrects_unit_scale_error():
    ctx = _ctx()
    ctx.current_question = "第二期最大图斑面积是多少"
    ctx.effective_question = "第二期最大图斑面积是多少"
    ctx.turn_tool_messages = [_tool("第二期最大图斑面积 436612.85 平方米")]
    agent = SQLAgent()
    final = AssistantMessage(content="第二期最大图斑面积约 654,919.28 亩。", model="test")
    result = await agent.finalize_answer(ctx, final)
    guard = ctx.answer_metadata["number_guard"]
    assert guard["blocked"] is False
    assert guard["ok"] is True
    assert "654.92" in result.content
    assert "654,919.28" not in result.content


@pytest.mark.asyncio
async def test_sql_agent_finalize_rewrites_unsafe_answer_once():
    class _RewriteLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
            self.calls += 1
            return AssistantMessage(content="该占比无法从查询结果确认，请以表格为准。", model=model)

    ctx = _ctx()
    ctx.current_question = "建设用地占比是多少"
    ctx.effective_question = "建设用地占比是多少"
    ctx.turn_tool_messages = [
        _tool("建设用地 2954687.33 平方米；总面积 20586122.73 平方米")
    ]
    ctx.llm = _RewriteLLM()
    agent = SQLAgent()
    final = AssistantMessage(content="建设用地占比 18.2%。", model="test")
    result = await agent.finalize_answer(ctx, final)
    guard = ctx.answer_metadata["number_guard"]
    assert ctx.llm.calls == 1
    assert guard["rewrite_attempted"] is True
    assert guard["blocked"] is False
    assert "无法从查询结果确认" in result.content



@pytest.mark.asyncio
async def test_structured_followup_uses_llm_when_rules_cannot_resolve():
    from geoagent.core.llm import AssistantMessage
    from geoagent.dialogue import resolve_followup_smart

    class _LLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
            self.calls += 1
            return AssistantMessage(
                content='{"is_followup":true,"resolved_question":"耕地面积按县统计",'
                        '"changed_fields":["dimension"],"confidence":0.86}',
                model=model,
            )

    ctx = _ctx()
    ctx.query_state = {"effective_question": "耕地面积是多少"}
    ctx.llm = _LLM()
    effective, info = await resolve_followup_smart(ctx, "那它呢")
    assert info["kind"] == "structured"
    assert effective == "耕地面积按县统计"
    assert info["confidence"] == 0.86
    assert ctx.llm.calls == 1


@pytest.mark.asyncio
async def test_structured_followup_falls_back_on_bad_json():
    from geoagent.core.llm import AssistantMessage
    from geoagent.dialogue import resolve_followup_smart

    class _LLM:
        async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
            return AssistantMessage(content="不是 JSON", model=model)

    ctx = _ctx()
    ctx.query_state = {"effective_question": "耕地面积是多少"}
    ctx.llm = _LLM()
    effective, info = await resolve_followup_smart(ctx, "那它呢")
    assert effective == "那它呢"
    assert info == {}
