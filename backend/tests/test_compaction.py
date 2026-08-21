from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from geoagent.core.agent import Agent
from geoagent.core.context import ConversationContext
from geoagent.core.llm import AssistantMessage, ToolCall
from geoagent.memory.compactor import ContextCompactor
from geoagent.memory.session import ConversationSession
from geoagent.tools import ToolExecutor
from geoagent.tools.builtin import get_builtin_tools


class StubLLM:
    def __init__(self, summary: str = "压缩摘要") -> None:
        self.summary = summary
        self.calls = 0

    async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
        self.calls += 1
        system = next(
            (m["content"] for m in messages if m.get("role") == "system"),
            "",
        )
        if "summarizer" in system:
            return AssistantMessage(content=self.summary, model=model)
        return AssistantMessage(content="ok", model=model)


def test_tool_result_budget_persists_large_results(tmp_path: Path) -> None:
    compactor = ContextCompactor(transcripts_dir=tmp_path)
    compactor.TOOL_RESULT_BUDGET_CHARS = 1000
    compactor.LARGE_RESULT_CHAR_LIMIT = 400
    messages = [
        {"role": "tool", "tool_call_id": "big", "content": "x" * 500},
        {"role": "tool", "tool_call_id": "small", "content": "y" * 300},
        {"role": "tool", "tool_call_id": "small2", "content": "z" * 300},
    ]
    compactor.tool_result_budget(messages)
    big = next(m for m in messages if m["tool_call_id"] == "big")
    assert big["content"].startswith("Full output: ")
    assert (tmp_path / "tool-results" / "big.txt").exists()


def test_snip_compact_archives_middle(tmp_path: Path) -> None:
    compactor = ContextCompactor(transcripts_dir=tmp_path)
    messages = [{"role": "user", "content": f"m{i}"} for i in range(60)]
    compactor.snip_compact(messages)
    assert len(messages) == compactor.MAX_MESSAGES + 1
    assert any("archived at" in str(m.get("content", "")) for m in messages)
    assert list((tmp_path / "transcripts").glob("*-snip-*.json"))


def test_micro_compact_shortens_consumed_results() -> None:
    compactor = ContextCompactor()
    compactor.KEEP_RECENT_RESULTS = 3
    messages: list[dict] = []
    for i in range(5):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"c{i}", "function": {"name": "t", "arguments": "{}"}}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 200})
        messages.append({"role": "assistant", "content": f"after {i}"})
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c5", "function": {"name": "t", "arguments": "{}"}}],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "c5", "content": "y" * 200})

    compactor.micro_compact(messages)
    # c0、c1 已被读取且超出最近 3 条 → 占位符；c2/c3/c4 保留；c5 未读保留。
    assert messages[1]["content"] == "[Earlier tool result omitted.]"
    assert messages[4]["content"] == "[Earlier tool result omitted.]"
    assert messages[7]["content"] == "x" * 200
    assert messages[10]["content"] == "x" * 200
    assert messages[13]["content"] == "x" * 200
    assert messages[16]["content"] == "y" * 200


@pytest.mark.asyncio
async def test_compact_history_replaces_with_summary(tmp_path: Path) -> None:
    compactor = ContextCompactor(transcripts_dir=tmp_path)
    compactor.CONTEXT_CHAR_LIMIT = 100
    llm = StubLLM(summary="状态摘要")
    messages = [{"role": "user", "content": f"m{i}"} for i in range(30)]
    ok = await compactor.compact_history(messages, "当前问题", llm, "qwen3.7-flash")
    assert ok is True
    assert len(messages) == 1
    text = messages[0]["content"]
    assert "Current user request: 当前问题" in text
    assert "Conversation summary: 状态摘要" in text
    assert "Full transcript:" in text
    assert list((tmp_path / "transcripts").glob("*-compact-*.json"))


@pytest.mark.asyncio
async def test_reactive_compact_keeps_recent(tmp_path: Path) -> None:
    compactor = ContextCompactor(transcripts_dir=tmp_path)
    llm = StubLLM(summary="补救摘要")
    messages = [{"role": "user", "content": f"m{i}"} for i in range(12)]
    await compactor.reactive_compact(messages, "当前问题", llm, "qwen3.7-flash")
    assert len(messages) == compactor.KEEP_RECENT_MESSAGES + 1
    assert messages[0]["content"].startswith("<Reactive compact>")
    assert "Conversation summary: 补救摘要" in messages[0]["content"]


@pytest.mark.asyncio
async def test_agent_loop_runs_four_step_pipeline(tmp_path: Path) -> None:
    session = ConversationSession()
    for i in range(12):
        session.add_message({"role": "user", "content": "x" * 5000})
    llm = StubLLM()
    ctx = ConversationContext(
        conversation_id="c1",
        session=session,
        model="qwen3.7-flash",
        llm=llm,
        store=None,
        memory=None,
        event_sink=None,
        transcripts_dir=tmp_path,
    )
    agent = Agent(name="t", system_prompt="You are a test agent.")
    _, final = await agent.exec(ctx, "hello")
    assert final.content == "ok"
    # 历史被 [Compacted] 摘要消息替换，随后追加助手回复。
    assert len(session.history()) == 2
    assert "Conversation summary" in str(session.history()[0].get("content", ""))
    assert llm.calls >= 2  # 一次摘要 + 一次主对话
    assert list((tmp_path / "transcripts").glob("*-compact-*.json"))


@pytest.mark.asyncio
async def test_compact_tool_requests_compaction() -> None:
    ctx = SimpleNamespace(compact_requested=False)
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(id="c1", name="compact", arguments={}),
        ctx=ctx,
    )
    assert not result.is_error
    assert ctx.compact_requested is True
    assert "压缩" in result.content


def test_session_orphan_cleanup() -> None:
    session = ConversationSession()
    session.add_message(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{}"}}],
        }
    )
    session.add_message({"role": "tool", "tool_call_id": "c1", "content": "r"})
    session.add_message({"role": "tool", "tool_call_id": "ghost", "content": "o"})
    session.drop_orphan_tool_messages()
    assert len(session.history()) == 2
