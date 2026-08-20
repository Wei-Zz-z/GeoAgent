from __future__ import annotations

import pytest

from geoagent.core.context import ConversationContext
from geoagent.core.llm import AssistantMessage, ToolCall
from geoagent.core.subagent import build_subagent_tools
from geoagent.memory.session import ConversationSession
from geoagent.tools import ToolExecutor
from geoagent.tools.builtin import get_builtin_tools


class StubLLM:
    def __init__(self, content: str = "子任务结果文本") -> None:
        self.content = content

    async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
        return AssistantMessage(content=self.content, model=model)


def make_parent_ctx() -> tuple[ConversationContext, list]:
    events: list = []

    async def sink(event) -> None:
        events.append(event)

    return (
        ConversationContext(
            conversation_id="c1",
            session=ConversationSession(),
            model="qwen3.7-flash",
            llm=StubLLM(),
            store=None,
            memory=None,
            event_sink=sink,
            skills=None,
            subagent_depth=0,
        ),
        events,
    )


@pytest.mark.asyncio
async def test_task_tool_runs_subagent_and_returns_final_text():
    ctx, events = make_parent_ctx()
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(
            id="t1",
            name="task",
            arguments={"prompt": "帮我读一下 config.py 并总结"},
        ),
        ctx=ctx,
    )
    assert not result.is_error
    assert "子任务完成" in result.content
    assert "子任务结果文本" in result.content
    assert any(e.type == "subagent_start" for e in events)
    assert any(e.type == "subagent_end" for e in events)
    assert len(ctx.subagents) == 1
    assert ctx.subagents[0]["status"] == "done"
    # 子 Agent 的消息不进入父会话（全新上下文）。
    assert ctx.session.history() == []


@pytest.mark.asyncio
async def test_task_tool_respects_depth_limit():
    ctx, _ = make_parent_ctx()
    ctx.subagent_depth = 2
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(id="t2", name="task", arguments={"prompt": "x"}),
        ctx=ctx,
    )
    assert result.is_error
    assert "depth limit" in result.content


def test_subagent_tools_exclude_task():
    names = {t.name for t in build_subagent_tools()}
    assert "task" not in names
    assert "todo_write" in names
    assert "load_skill" in names
