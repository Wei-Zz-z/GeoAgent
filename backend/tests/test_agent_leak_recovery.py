"""Agent 对「模型把工具调用写进正文」的兜底行为测试（不依赖真实 API / 数据库）。

覆盖三条路径：
1. 标记指向真实存在的工具 → 还原成工具调用继续执行，标记不进正文与历史；
2. 标记指向不存在的工具且正文仍有内容 → 只剥标记，不重试；
3. 整条回复只是一个发不出去的标记 → 纠正一次，不落库。
"""

from __future__ import annotations

from typing import Any

import pytest

from geoagent.core.agent import Agent
from geoagent.core.context import ConversationContext
from geoagent.core.llm import AssistantMessage
from geoagent.memory.session import ConversationSession
from geoagent.tools.registry import Tool


def _echo_tool() -> Tool:
    def echo(text: str) -> str:
        """回显文本。"""
        return f"echo:{text}"

    return Tool.build("echo", "回显文本", echo)


class ScriptedLLM:
    """按脚本顺序返回响应的桩模型；流式接口只做分片转发。"""

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []

    def _next(self, messages: list[dict[str, Any]]) -> AssistantMessage:
        self.calls.append(messages)
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]

    async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
        return self._next(messages)

    async def stream_chat(
        self,
        model,
        messages,
        tools=None,
        temperature=None,
        max_tokens=None,
        on_token=None,
    ):
        message = self._next(messages)
        if on_token is not None and message.content:
            for index in range(0, len(message.content), 12):
                await on_token(message.content[index : index + 12])
        return message


def make_ctx(replies: list[AssistantMessage]) -> tuple[ConversationContext, list[Any]]:
    events: list[Any] = []

    async def sink(event) -> None:
        events.append(event)

    ctx = ConversationContext(
        conversation_id="c1",
        session=ConversationSession(),
        model="qwen3.7-flash",
        llm=ScriptedLLM(replies),
        store=None,
        memory=None,
        event_sink=sink,
        skills=None,
    )
    return ctx, events


def _leak(name: str, params: dict[str, str]) -> str:
    body = "".join(
        f'<parameter name="{key}">{value}</parameter>' for key, value in params.items()
    )
    return f'<function_calls>\n<invoke name="{name}">\n{body}\n</invoke>\n</function_calls>'


@pytest.mark.asyncio
async def test_agent_executes_recovered_leaked_call() -> None:
    ctx, events = make_ctx(
        [
            AssistantMessage(
                content="先给结论。\n\n完整明细如下：\n\n" + _leak("echo", {"text": "hi"}),
                model="stub",
            ),
            AssistantMessage(content="结论已经生成。", model="stub"),
        ]
    )
    agent = Agent(name="t", system_prompt="测试 Agent", tools=[_echo_tool()])
    _, final = await agent.exec(ctx, "有多少")

    assert final.content == "结论已经生成。"
    tool_calls = [event for event in events if event.type == "tool_call"]
    assert [event.data["name"] for event in tool_calls] == ["echo"]
    assert tool_calls[0].data["arguments"] == {"text": "hi"}

    history = ctx.session.history()
    first_assistant = history[1]
    assert first_assistant["role"] == "assistant"
    assert first_assistant["content"] == "先给结论。\n\n完整明细如下："
    assert "function_calls" not in first_assistant["content"]
    assert first_assistant["tool_calls"][0]["function"]["name"] == "echo"
    # 工具真的执行了，结果进入历史。
    assert any(
        message.get("role") == "tool" and "echo:hi" in str(message.get("content"))
        for message in history
    )


@pytest.mark.asyncio
async def test_agent_strips_leak_without_retry_when_tool_unknown() -> None:
    ctx, events = make_ctx(
        [
            AssistantMessage(
                content="结论如下。\n\n" + _leak("not_a_tool", {"x": "1"}),
                model="stub",
            )
        ]
    )
    agent = Agent(name="t", system_prompt="测试 Agent", tools=[_echo_tool()])
    _, final = await agent.exec(ctx, "问题")

    assert final.content == "结论如下。"
    assert len(ctx.llm.calls) == 1  # 没有多余重试
    assert [event for event in events if event.type == "tool_call"] == []
    assert "function_calls" not in ctx.session.history()[1]["content"]


@pytest.mark.asyncio
async def test_agent_retries_once_when_reply_is_only_leak() -> None:
    ctx, _ = make_ctx(
        [
            AssistantMessage(content=_leak("not_a_tool", {"x": "1"}), model="stub"),
            AssistantMessage(content="这是修正后的回答。", model="stub"),
        ]
    )
    agent = Agent(name="t", system_prompt="测试 Agent", tools=[_echo_tool()])
    _, final = await agent.exec(ctx, "问题")

    assert final.content == "这是修正后的回答。"
    assert len(ctx.llm.calls) == 2
    assert any(
        "工具调用写成了正文标记" in str(message.get("content", ""))
        for message in ctx.session.history()
    )
    assert all(
        "function_calls" not in str(message.get("content", ""))
        for message in ctx.session.history()
    )


@pytest.mark.asyncio
async def test_agent_never_persists_empty_reply_after_repeated_leaks() -> None:
    """连续两次只回标记：第二次不再重试，给用户明确说明而不是空气泡。"""
    ctx, _ = make_ctx(
        [
            AssistantMessage(content=_leak("not_a_tool", {"x": "1"}), model="stub"),
            AssistantMessage(content=_leak("not_a_tool", {"x": "1"}), model="stub"),
        ]
    )
    agent = Agent(name="t", system_prompt="测试 Agent", tools=[_echo_tool()])
    _, final = await agent.exec(ctx, "问题")

    assert final.content.strip() != ""
    assert "function_calls" not in final.content
    stored = [message for message in ctx.session.history() if message.get("role") == "assistant"]
    assert all("function_calls" not in str(message.get("content", "")) for message in stored)