from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoagent.core.agent import Agent
from geoagent.core.llm import ToolCall
from geoagent.tools import ToolExecutor
from geoagent.tools.builtin import get_builtin_tools


def make_ctx() -> tuple[SimpleNamespace, list]:
    events: list = []

    async def sink(event) -> None:
        events.append(event)

    return SimpleNamespace(todos=[], event_sink=sink, emit=sink), events


@pytest.mark.asyncio
async def test_todo_write_replaces_list_and_emits_event():
    ctx, events = make_ctx()
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(
            id="t1",
            name="todo_write",
            arguments={
                "todos": [
                    {"content": "查表结构", "status": "in_progress"},
                    {"content": "跑查询"},
                    {"content": "写总结", "status": "completed"},
                ]
            },
        ),
        ctx=ctx,
    )
    assert not result.is_error
    assert len(ctx.todos) == 3
    assert ctx.todos[0]["status"] == "in_progress"
    assert "[>]" in result.content
    assert "[x] 写总结" in result.content
    assert any(e.type == "todo" for e in events)
    assert events[-1].data["todos"] == ctx.todos


@pytest.mark.asyncio
async def test_todo_write_rejects_over_20_items():
    ctx, _ = make_ctx()
    executor = ToolExecutor(get_builtin_tools())
    todos = [{"content": f"step {i}"} for i in range(21)]
    result = await executor.execute(
        ToolCall(id="t2", name="todo_write", arguments={"todos": todos}),
        ctx=ctx,
    )
    assert result.is_error
    assert "参数校验失败" in result.content
    assert ctx.todos == []


@pytest.mark.asyncio
async def test_todo_write_rejects_multiple_in_progress():
    ctx, _ = make_ctx()
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(
            id="t3",
            name="todo_write",
            arguments={
                "todos": [
                    {"content": "a", "status": "in_progress"},
                    {"content": "b", "status": "in_progress"},
                ]
            },
        ),
        ctx=ctx,
    )
    assert result.is_error
    assert "in_progress" in result.content


@pytest.mark.asyncio
async def test_todo_write_rejects_empty_content():
    ctx, _ = make_ctx()
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(
            id="t4",
            name="todo_write",
            arguments={"todos": [{"content": "  "}]},
        ),
        ctx=ctx,
    )
    assert result.is_error


def test_agent_merges_builtin_tools_and_guidance():
    agent = Agent(name="test", system_prompt="You are a test assistant.")
    names = {t.name for t in agent.tools}
    assert "todo_write" in names
    assert "todo_write" in agent.system_prompt
