
"""标准问题预匹配测试：不依赖真实数据库、网络与模型。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from geoagent.core.llm import ToolCall
from geoagent.datasets import DatasetPeriod, DatasetRegistry
from geoagent.prematch import resolve_turn_dataset, try_standard_answer
from pydantic import BaseModel

from geoagent.tools import ToolExecutor
from geoagent.tools.registry import Tool
from geoagent.tools.result import ToolResult
from geoagent.tools.stat import get_stat_tools

TABLE = 'data."2026_1_change_landuse"'
OTHER_TABLE = 'data."2026_2_change_landuse"'

LIBRARY = {
    "name": "test-library",
    "version": "test",
    "table": "{table}",
    "entries": [
        {
            "id": "total_summary",
            "question": "全库共有多少个变化图斑、总面积是多少",
            "aliases": ["一共有多少图斑", "全部图斑的总量"],
            "keywords": ["总数", "总面积", "全库"],
            "intent": "全库图斑总数与总面积",
            "strategy": "sql",
            "sql": 'SELECT count(*) AS n, sum("MJ") AS area FROM {table}',
            "unit": "平方米",
            "answer_hint": "总数与总面积必须来自同一次汇总查询",
        },
    ],
}


class FakeSession:
    """最小会话实现：只记录消息顺序，便于断言落库形状。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_message(self, message: dict) -> None:
        self.messages.append(message)


class FakeGateway:
    def __init__(self, rows=None) -> None:
        self.sql: list[str] = []
        self.rows = rows if rows is not None else [{"n": 3, "area": 120.5}]

    async def run_sql(self, sql, conversation_id="", tool_call_id=""):
        self.sql.append(sql)
        # 预匹配会先取当期表的县级行政区清单（用于识别问题里的"某个县"）
        if "DISTINCT" in sql:
            return {
                "columns": ["xmc"],
                "rows": [{"xmc": "缙云县"}, {"xmc": "莲都区"}],
                "row_count": 2,
                "truncated": False,
            }
        columns = list(self.rows[0]) if self.rows else []
        return {"columns": columns, "rows": self.rows, "row_count": len(self.rows), "truncated": False}


class FakeCtx:
    def __init__(self, gateway=None, skills_dir=None, prematch="execute", dataset_id="") -> None:
        self.pg = gateway
        self.session = FakeSession()
        self.events: list = []
        self.model = "test-model"
        self.route = "sql"
        self.conversation_id = "conv-1"
        self.dataset_id = dataset_id
        self.dataset = None
        self.period_notice = ""
        self.prematch_hint = ""
        self.skills = SimpleNamespace(skills_dir=skills_dir)
        self.datasets = DatasetRegistry(
            periods=(
                DatasetPeriod(id="2026_1", label="2026年第一期", table=TABLE, year=2026, round=1),
            ),
            default_id="2026_1",
        )
        self.settings = SimpleNamespace(qa_prematch=prematch, qa_prematch_confidence=0.5)

    async def emit(self, event) -> None:
        self.events.append(event)


def _write_library(tmp_path, library=None):
    skills = tmp_path / "skills"
    asset = skills / "qa-library" / "assets"
    asset.mkdir(parents=True)
    (asset / "question_library.json").write_text(
        json.dumps(library or LIBRARY, ensure_ascii=False), encoding="utf-8"
    )
    return skills


@pytest.mark.asyncio
async def test_prematch_executes_standard_sql_with_bound_table(tmp_path):
    ctx = FakeCtx(gateway=FakeGateway(), skills_dir=_write_library(tmp_path))
    resolve_turn_dataset(ctx, "全库共有多少个变化图斑、总面积是多少")
    final = await try_standard_answer(ctx, "全库共有多少个变化图斑、总面积是多少")

    assert final is not None
    assert ctx.pg.sql and TABLE in ctx.pg.sql[0] and "{table}" not in ctx.pg.sql[0]
    assert "统计口径" in final.content and "2026年第一期" in final.content
    # 事件形状与普通工具轮次一致，前端无需改动
    types = [event.type for event in ctx.events]
    assert types == ["standard_match", "tool_call", "tool_result", "artifact", "message"]
    assert [m["role"] for m in ctx.session.messages] == ["user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
async def test_prematch_uses_resolved_period_for_tools(tmp_path):
    """确定性统计工具必须按本轮期次取表，而不是写死第一期。"""
    registry = DatasetRegistry(
        periods=(DatasetPeriod(id="2026_2", label="2026年第二期", table=OTHER_TABLE),),
        default_id="2026_2",
    )
    gateway = FakeGateway(rows=[{"code": "01", "name": "耕地", "n": 1, "area_m2": 10.0}])
    ctx = FakeCtx(gateway=gateway)
    ctx.datasets = registry
    ctx.dataset = registry.default
    executor = ToolExecutor(get_stat_tools())
    result = await executor.execute(ToolCall(id="c1", name="summarize_by_type", arguments={}), ctx=ctx)
    assert result.is_error is False
    assert gateway.sql and all(OTHER_TABLE in sql for sql in gateway.sql)


@pytest.mark.asyncio
async def test_explicit_dataset_id_wins_over_question_text(tmp_path):
    """前端数据表选择框下发的 dataset_id 优先于问题文本里的期次说法。"""
    ctx = FakeCtx(gateway=FakeGateway(), skills_dir=_write_library(tmp_path), dataset_id="2026_2")
    ctx.datasets = DatasetRegistry(
        periods=(
            DatasetPeriod(id="2026_1", label="2026年第一期", table=TABLE, year=2026, round=1),
            DatasetPeriod(id="2026_2", label="2026年第二期", table=OTHER_TABLE, year=2026, round=2),
        ),
        default_id="2026_1",
    )
    lookup = resolve_turn_dataset(ctx, "2026年第一期全库图斑总数是多少")
    assert lookup.dataset is not None and lookup.dataset.id == "2026_2"
    assert ctx.dataset.table == OTHER_TABLE
    assert ctx.period_notice == ""


@pytest.mark.asyncio
async def test_prematch_falls_back_to_free_sql_when_unmatched(tmp_path):
    ctx = FakeCtx(gateway=FakeGateway(), skills_dir=_write_library(tmp_path))
    resolve_turn_dataset(ctx, "帮我把这个县的路网密度算一下")
    final = await try_standard_answer(ctx, "帮我把这个县的路网密度算一下")
    assert final is None
    assert ctx.pg.sql == []
    match_events = [e for e in ctx.events if e.type == "standard_match"]
    assert match_events and match_events[0].data["matched"] is False


@pytest.mark.asyncio
async def test_prematch_refuses_unregistered_period(tmp_path):
    ctx = FakeCtx(gateway=FakeGateway(), skills_dir=_write_library(tmp_path))
    resolve_turn_dataset(ctx, "2026年第二期全库图斑总数是多少")
    final = await try_standard_answer(ctx, "2026年第二期全库图斑总数是多少")
    assert final is not None
    assert "还没有登记数据表" in final.content
    assert ctx.pg.sql == []


@pytest.mark.asyncio
async def test_prematch_skips_report_generation_requests(tmp_path):
    ctx = FakeCtx(gateway=FakeGateway(), skills_dir=_write_library(tmp_path))
    resolve_turn_dataset(ctx, "请生成本期快报")
    assert await try_standard_answer(ctx, "请生成本期快报") is None
    assert ctx.pg.sql == []


@pytest.mark.asyncio
async def test_hint_mode_never_executes(tmp_path):
    ctx = FakeCtx(gateway=FakeGateway(), skills_dir=_write_library(tmp_path), prematch="hint")
    resolve_turn_dataset(ctx, "全库共有多少个变化图斑、总面积是多少")
    assert await try_standard_answer(ctx, "全库共有多少个变化图斑、总面积是多少") is None
    assert ctx.pg.sql == []
    assert "标准问题" in ctx.prematch_hint


def test_prematch_disabled_by_setting(tmp_path):
    import asyncio

    ctx = FakeCtx(gateway=FakeGateway(), skills_dir=_write_library(tmp_path), prematch="off")
    resolve_turn_dataset(ctx, "全库共有多少个变化图斑、总面积是多少")
    assert asyncio.run(try_standard_answer(ctx, "全库共有多少个变化图斑、总面积是多少")) is None


# ---------------------------------------------------------------- SQLAgent 接线
class ScriptedLLM:
    """按脚本顺序返回响应的桩模型；不触网。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def _next(self, messages):
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]

    async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
        return self._next(messages)

    async def stream_chat(self, model, messages, tools=None, temperature=None,
                          max_tokens=None, on_token=None):
        message = self._next(messages)
        if on_token is not None and message.content:
            await on_token(message.content)
        return message


def _fake_skills(skills_dir):
    """最小技能加载器桩：Agent 会调用 catalog_prompt()。"""
    return SimpleNamespace(skills_dir=skills_dir, catalog_prompt=lambda: "")


def _agent_ctx(replies, gateway, skills_dir):
    from geoagent.core.context import ConversationContext
    from geoagent.core.llm import AssistantMessage  # noqa: F401  （保持导入显式）
    from geoagent.memory.session import ConversationSession

    probe = FakeCtx(gateway=gateway, skills_dir=skills_dir)
    return ConversationContext(
        conversation_id="conv-agent",
        session=ConversationSession(),
        model="test-model",
        llm=ScriptedLLM(replies),
        store=None,
        memory=None,
        event_sink=None,
        skills=_fake_skills(skills_dir),
        pg=gateway,
        datasets=probe.datasets,
        settings=probe.settings,
    )


@pytest.mark.asyncio
async def test_sql_agent_falls_back_to_free_sql_when_unmatched(tmp_path):
    """未命中标准问题时仍走 LLM 工具循环，并把当期期次写进提示词。"""
    from geoagent.agents.sql import SQLAgent
    from geoagent.core.llm import AssistantMessage

    gateway = FakeGateway()
    ctx = _agent_ctx([AssistantMessage(content="自由查询回答")], gateway, _write_library(tmp_path))
    action, final = await SQLAgent().exec(ctx, "帮我把这个县的路网密度算一下")
    assert action == "default"
    assert final.content == "自由查询回答"
    system_prompt = ctx.llm.calls[0][0]["content"]
    assert "当前期次" in system_prompt and TABLE in system_prompt


@pytest.mark.asyncio
async def test_sql_agent_answers_from_standard_question_without_llm(tmp_path):
    """命中标准问题时直接确定性作答，不再调用模型。"""
    from geoagent.agents.sql import SQLAgent
    from geoagent.core.llm import AssistantMessage

    gateway = FakeGateway()
    ctx = _agent_ctx([AssistantMessage(content="不该出现")], gateway, _write_library(tmp_path))
    _, final = await SQLAgent().exec(ctx, "全库共有多少个变化图斑、总面积是多少")
    assert ctx.llm.calls == []
    assert gateway.sql and TABLE in gateway.sql[0]
    assert "数量：3" in final.content
    assert "数据期次：" in final.content
    assert not final.content.startswith("#")

# ---------------------------------------------------------------- 行政区下钻


class _WarningParams(BaseModel):
    county: str = ""
    max_features: int = 500


def _county_library(strategy: str, sql: str) -> dict:
    return {
        "name": "test-county-library",
        "version": "test",
        "table": "{table}",
        "entries": [
            {
                "id": "illegal_yongnong_construction",
                "question": "永久基本农田范围内疑似转为建设用地的图斑有多少、面积多大",
                "aliases": ["违规占用永久基本农田的图斑有哪些"],
                "keywords": ["永久基本农田", "图斑", "违规"],
                "intent": "疑似占用永农的建设图斑",
                "strategy": strategy,
                "tool": "warning_yongnong_polygons",
                "sql": sql,
                "unit": "亩",
                "answer_hint": "疑似口径",
            }
        ],
    }


def _fake_warning_executor(calls: list[str]) -> ToolExecutor:
    async def fake_warning(ctx, county="", max_features=500):
        calls.append(county)
        return ToolResult(
            tool_call_id="",
            name="warning_yongnong_polygons",
            content=f"县级={county}",
        )

    tool = Tool.build("warning_yongnong_polygons", "测试用预警工具", fake_warning, _WarningParams)
    return ToolExecutor([tool])


def test_mentioned_regions_ignores_generic_words():
    from geoagent.prematch import _mentioned_regions

    names = ["缙云县", "莲都区"]
    assert _mentioned_regions("统计每个县的耕地面积", names) == []
    assert _mentioned_regions("哪些县耕地减少最多", names) == []
    assert _mentioned_regions("统计缙云县违规占用永久基本农田的图斑", names) == ["缙云县"]


@pytest.mark.asyncio
async def test_county_scope_is_passed_to_tool_entry(tmp_path, monkeypatch):
    """问题限定了某个县时，预匹配把 county 传给工具，而不是统计全库。"""
    calls: list[str] = []
    monkeypatch.setattr("geoagent.prematch._executor", lambda: _fake_warning_executor(calls))
    ctx = FakeCtx(
        gateway=FakeGateway(),
        skills_dir=_write_library(tmp_path, _county_library("tool", "")),
    )
    question = "统计一下缙云县违规占用永久基本农田的图斑有哪些"
    resolve_turn_dataset(ctx, question)

    final = await try_standard_answer(ctx, question)

    assert final is not None
    assert calls == ["缙云县"]
    match_event = next(event for event in ctx.events if event.type == "standard_match")
    assert match_event.data["matched"] is True
    assert match_event.data["arguments"] == {"county": "缙云县"}
    assert ctx.session.messages[1]["tool_calls"][0]["function"]["arguments"] == (
        '{"county": "缙云县"}'
    )


@pytest.mark.asyncio
async def test_county_scope_blocks_entry_that_cannot_filter(tmp_path, monkeypatch):
    """条目表达不了区县筛选时不套用整表口径，转自由 SQL。"""
    calls: list[str] = []
    monkeypatch.setattr("geoagent.prematch._executor", lambda: _fake_warning_executor(calls))
    gateway = FakeGateway()
    ctx = FakeCtx(
        gateway=gateway,
        skills_dir=_write_library(
            tmp_path,
            _county_library("sql", 'SELECT count(*) AS n FROM {table}'),
        ),
    )
    question = "统计一下缙云县违规占用永久基本农田的图斑有哪些"
    resolve_turn_dataset(ctx, question)

    final = await try_standard_answer(ctx, question)

    assert final is None
    assert calls == []
    # 只可能跑过"取行政区清单"的查询（结果还会被缓存），没有执行整表口径的条目 SQL
    assert not [sql for sql in gateway.sql if "DISTINCT" not in sql]
    match_event = next(event for event in ctx.events if event.type == "standard_match")
    assert match_event.data["matched"] is False
    assert "缙云县" in match_event.data["notice"]
