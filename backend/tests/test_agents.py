"""会话级智能体选择测试：指定智能体时不再走 LLM 路由。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoagent.agents.graph import AGENT_SPECS, agent_catalog, is_known_agent, run_turn
from geoagent.core.context import ConversationContext
from geoagent.core.llm import AssistantMessage
from geoagent.memory.session import ConversationSession


class RecordingLLM:
    """记录每次调用发出去的消息，便于断言走的是哪条链路。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def _next(self, messages):
        self.calls.append(messages)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]

    async def chat(self, model, messages, tools=None, temperature=None, max_tokens=None):
        return self._next(messages)

    async def stream_chat(self, model, messages, tools=None, temperature=None,
                          max_tokens=None, on_token=None):
        message = self._next(messages)
        if on_token is not None and message.content:
            await on_token(message.content)
        return message


def _ctx(llm: RecordingLLM) -> ConversationContext:
    ctx = ConversationContext(
        conversation_id="conv-agent",
        session=ConversationSession(),
        model="test-model",
        llm=llm,
        store=None,
        memory=None,
        event_sink=None,
        skills=SimpleNamespace(skills_dir=None, catalog_prompt=lambda: ""),
        pg=None,
        datasets=None,
        settings=SimpleNamespace(qa_prematch="off", qa_prematch_confidence=0.5),
    )
    ctx.events = []

    async def emit(event) -> None:
        ctx.events.append(event)

    ctx.emit = emit
    return ctx


def test_agent_catalog_covers_ui_targets():
    assert [spec.id for spec in AGENT_SPECS] == ["sql", "elder_care", "chat"]
    catalog = agent_catalog()
    assert catalog[0]["label"] == "土地变化统计问答"
    assert all(item["description"] for item in catalog)
    assert is_known_agent("chat") is True
    assert is_known_agent("not-exist") is False


@pytest.mark.asyncio
async def test_selected_agent_skips_router():
    """会话选了智能体就直连，不调用路由器。"""
    llm = RecordingLLM([AssistantMessage(content="你好，我是通用对话")])
    ctx = _ctx(llm)

    final = await run_turn(ctx, "你好", agent_id="chat")

    assert final.content == "你好，我是通用对话"
    assert len(llm.calls) == 1
    system_prompt = llm.calls[0][0]["content"]
    assert "地理空间分析助手" in system_prompt
    assert "意图路由器" not in system_prompt

    route_events = [event for event in ctx.events if event.type == "route"]
    assert route_events and route_events[0].data["target"] == "chat"
    assert "选择" in route_events[0].data["reason"]
    assert ctx.route == "chat"
    assert ctx.agent_id == "chat"


@pytest.mark.asyncio
async def test_unknown_agent_falls_back_to_router_graph():
    """未指定/未知智能体时仍走 RouterNode 兜底，保证老客户端可用。"""
    llm = RecordingLLM([AssistantMessage(content="路由兜底回答")])
    ctx = _ctx(llm)

    final = await run_turn(ctx, "你好", agent_id="not-exist")

    assert final.content
    assert "意图路由器" in llm.calls[0][0]["content"]

def test_agents_endpoint_lists_targets():
    """/api/agents 供前端会话旁的智能体选择框渲染。"""
    from fastapi.testclient import TestClient

    from geoagent.server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/agents")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["agents"]] == ["sql", "elder_care", "chat"]
    assert payload["default_id"] == "sql"
